"""
定时调度模块
使用 APScheduler 定期执行抓取 → 过滤 → 推送任务链
"""

import asyncio
from datetime import datetime
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from src.models import Course, PushLog, get_session, init_db, FilterConfig
from src.scraper import create_browser_context, scrape_courses, save_courses_to_db
from src.auth import ensure_logged_in
from src.filters import filter_courses, load_filter_config
from src.push.email_push import send_email_notification, send_enroll_reminder_email
from src.push.rss_feed import generate_rss_feed
from src.enroll import auto_enroll_if_enabled
from src.push.telegram_bot import send_batch_notifications, send_daily_summary_notification, send_reminder_telegram
from src.push.rss_feed import generate_rss_feed
from src.enroll import auto_enroll_if_enabled

# 全局调度器实例
scheduler = AsyncIOScheduler()

# 全局运行状态
run_status = {
    "last_run": None,
    "last_success": None,
    "total_runs": 0,
    "total_new_courses": 0,
    "total_pushed": 0,
    "is_running": False,
    "last_error": None,
    "last_daily_summary": None,
}

# 全局浏览器实例（避免每次重新启动）
_browser_state = {
    "pw": None,
    "browser": None,
    "context": None,
    "page": None,
}


async def _close_browser_local(pw, browser):
    """局部关闭浏览器（不依赖全局状态）"""
    try:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    except Exception as e:
        logger.warning(f"关闭浏览器时出错: {e}")


async def run_scrape_task():
    """
    核心任务：登录 → 抓取 → 入库 → 过滤 → 推送 → (可选)自动选课
    每次调用创建全新浏览器，规避跨 event loop 的对象共享问题
    """
    if run_status["is_running"]:
        logger.warning("上一轮任务仍在运行，跳过本次")
        return

    run_status["is_running"] = True
    run_status["last_run"] = datetime.now()
    run_status["total_runs"] += 1

    pw = browser = context = page = None
    try:
        logger.info("=" * 50)
        logger.info(f"开始第 {run_status['total_runs']} 轮抓取任务")

        # 1. 创建全新浏览器并确保登录
        pw, browser, context, page = await create_browser_context()
        logged_in = await ensure_logged_in(page)
        if not logged_in:
            run_status["last_error"] = "登录失败"
            logger.error("登录失败，跳过本轮任务")
            return

        # 同步课程生命周期
        _sync_course_lifecycle()

        # 2. 抓取课程
        courses_data = await scrape_courses(page)
        if not courses_data:
            logger.info("未抓取到任何课程")
            run_status["last_success"] = datetime.now()
            return

        # 3. 保存到数据库（去重）
        new_course_ids = save_courses_to_db(courses_data)
        _sync_course_lifecycle()
        run_status["total_new_courses"] += len(new_course_ids)

        if not new_course_ids:
            logger.info("没有新课程，本轮无需推送")
            run_status["last_success"] = datetime.now()
            return

        logger.info(f"发现 {len(new_course_ids)} 门新课程")

        # 从数据库查询新课程
        session = get_session()
        try:
            db_courses = session.query(Course).filter(Course.id.in_(new_course_ids)).all()

            # 4. 过滤
            config = load_filter_config()
            filtered = filter_courses(db_courses, config)
            passed_courses = [c for c, _ in filtered]

            if not passed_courses:
                logger.info("所有新课程均被过滤，无需推送")
                run_status["last_success"] = datetime.now()
                return

            logger.info(f"{len(passed_courses)} 门课程通过过滤")

            # 5. 推送
            pushed_count = 0

            if config.daily_summary_enabled:
                logger.info("每日汇总模式已开启：本轮不即时推送，等待每日汇总任务")
            else:
                # Telegram 推送
                if config.telegram_enabled:
                    count = await send_batch_notifications(passed_courses)
                    pushed_count += count
                    _log_push(passed_courses, "telegram", count)

                # 邮件推送
                if config.email_enabled:
                    ok = await send_email_notification(passed_courses)
                    if ok:
                        pushed_count += len(passed_courses)
                        _log_push(passed_courses, "email", len(passed_courses))

                if pushed_count > 0:
                    for course in passed_courses:
                        course.pushed = True
                    session.commit()

            run_status["total_pushed"] += pushed_count

            # 6. 自动选课（如果开启）
            await auto_enroll_if_enabled(page, passed_courses)

        finally:
            session.close()

        run_status["last_success"] = datetime.now()
        run_status["last_error"] = None
        logger.info(f"本轮任务完成: 推送 {pushed_count} 条通知")

    except Exception as e:
        run_status["last_error"] = str(e)
        logger.error(f"抓取任务出错: {e}")
    finally:
        run_status["is_running"] = False
        await _close_browser_local(pw, browser)


async def run_daily_summary_task():
    """每日汇总推送任务：将未推送且通过过滤的课程汇总后发送"""
    session = get_session()
    try:
        config = load_filter_config()
        if not config.daily_summary_enabled:
            logger.info("每日汇总模式未启用，跳过每日汇总任务")
            return

        pending_courses = (
            session.query(Course)
            .filter(Course.pushed == False)  # noqa: E712
            .filter(Course.expired == False)  # noqa: E712
            .order_by(Course.first_seen.desc())
            .limit(500)
            .all()
        )

        if not pending_courses:
            logger.info("每日汇总：没有待推送课程")
            return

        filtered = filter_courses(pending_courses, config)
        passed_courses = [c for c, _ in filtered]
        if not passed_courses:
            logger.info("每日汇总：待推送课程均未通过过滤")
            return

        pushed_count = 0

        if config.telegram_enabled:
            ok = await send_daily_summary_notification(passed_courses)
            if ok:
                pushed_count += len(passed_courses)
                _log_push(passed_courses, "daily_telegram", len(passed_courses))

        if config.email_enabled:
            ok = await send_email_notification(passed_courses)
            if ok:
                pushed_count += len(passed_courses)
                _log_push(passed_courses, "daily_email", len(passed_courses))

        if pushed_count > 0:
            for course in passed_courses:
                course.pushed = True
            session.commit()
            run_status["total_pushed"] += pushed_count
            run_status["last_daily_summary"] = datetime.now()
            logger.info(f"每日汇总推送完成：{len(passed_courses)} 门课程")
        else:
            logger.warning("每日汇总推送失败：未通过任何推送通道成功发送")
    except Exception as e:
        logger.error(f"每日汇总任务出错: {e}")
    finally:
        session.close()


def _log_push(courses, push_type, count):
    """记录推送日志"""
    session = get_session()
    try:
        for course in courses[:count]:
            log = PushLog(
                course_id=course.id,
                push_type=push_type,
                pushed_at=datetime.now(),
                success=True,
            )
            session.add(log)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"记录推送日志失败: {e}")
    finally:
        session.close()


async def check_course_reminders():
    """检查并发送临近的选课提醒（每分钟执行）"""
    from src.models import CourseReminder, EmailSubscriber
    session = get_session()
    try:
        now = datetime.now()
        # 查找未发送的提醒
        pending_reminders = session.query(CourseReminder).filter_by(sent=False).all()
        if not pending_reminders:
            return

        for reminder in pending_reminders:
            course = session.query(Course).filter_by(id=reminder.course_id).first()
            sub = session.query(EmailSubscriber).filter_by(id=reminder.subscriber_id, active=True).first()
            
            if not course or not sub:
                reminder.sent = True  # 无效数据，标记为已发送
                continue
                
            if not course.enroll_start:
                continue

            # 计算现在到选课开始还有多少分钟
            time_diff = course.enroll_start - now
            minutes_left = time_diff.total_seconds() / 60

            # 如果剩余时间 <= 设定的提醒时间（加上 1 分钟宽限，防止刚好跳过），且尚未过期
            if 0 < minutes_left <= (reminder.remind_before_minutes + 1):
                try:
                    # 分别尝试发邮件和 Telegram
                    send_enroll_reminder_email(sub.email, course)
                    await send_reminder_telegram(course)
                    
                    reminder.sent = True
                    logger.info(f"已发送选课提醒: {sub.email} -> {course.name}")
                except Exception as e:
                    logger.error(f"发送选课提醒失败 {sub.email} -> {course.name}: {e}")
            elif minutes_left <= 0:
                # 已经过了选课时间，标记为已发送
                reminder.sent = True

        session.commit()
    except Exception as e:
        logger.error(f"检查选课提醒出错: {e}")
    finally:
        session.close()


def start_scheduler(interval_minutes: int = 10):
    """启动定时调度器"""
    scheduler.add_job(
        run_scrape_task,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scrape_task",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        check_course_reminders,
        trigger=IntervalTrigger(minutes=1),
        id="course_reminders_task",
        replace_existing=True,
    )
    _configure_daily_summary_job()
    scheduler.start()
    logger.info(f"定时调度已启动，每 {interval_minutes} 分钟执行一次抓取任务")


def update_scheduler_interval(interval_minutes: int):
    """更新调度间隔"""
    try:
        scheduler.reschedule_job(
            "scrape_task",
            trigger=IntervalTrigger(minutes=interval_minutes),
        )
        logger.info(f"调度间隔已更新为 {interval_minutes} 分钟")
    except Exception as e:
        logger.error(f"更新调度间隔失败: {e}")


def update_daily_summary_schedule():
    """更新每日汇总任务调度"""
    _configure_daily_summary_job()


def _configure_daily_summary_job():
    """根据配置启用/禁用每日汇总定时任务"""
    session = get_session()
    try:
        config = session.query(FilterConfig).first()
        if not config:
            return

        if scheduler.get_job("daily_summary_task"):
            scheduler.remove_job("daily_summary_task")

        if not config.daily_summary_enabled:
            logger.info("每日汇总任务未启用")
            return

        hour, minute = _parse_daily_time(config.daily_summary_time)
        scheduler.add_job(
            run_daily_summary_task,
            trigger=CronTrigger(hour=hour, minute=minute),
            id="daily_summary_task",
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"每日汇总任务已启用，每天 {hour:02d}:{minute:02d} 执行")
    except Exception as e:
        logger.error(f"配置每日汇总任务失败: {e}")
    finally:
        session.close()


def _sync_course_lifecycle():
    """同步课程生命周期：到期课程标记为 expired"""
    session = get_session()
    try:
        now = datetime.now()
        active_courses = (
            session.query(Course)
            .filter(Course.expired == False)  # noqa: E712
            .all()
        )

        expired_count = 0
        for course in active_courses:
            if course.enroll_end and course.enroll_end < now:
                course.expired = True
                expired_count += 1

        if expired_count > 0:
            session.commit()
            logger.info(f"课程生命周期同步完成：新增过期 {expired_count} 门")
    except Exception as e:
        session.rollback()
        logger.error(f"同步课程生命周期失败: {e}")
    finally:
        session.close()


def _parse_daily_time(time_text: str) -> tuple:
    """解析 HH:MM 格式时间，非法时回退到 21:00"""
    try:
        parts = (time_text or "").strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except Exception:
        pass
    logger.warning(f"无效的每日汇总时间: {time_text}，已回退为 21:00")
    return 21, 0


def get_run_status() -> dict:
    """获取运行状态"""
    return {
        "last_run": run_status["last_run"].strftime("%Y-%m-%d %H:%M:%S") if run_status["last_run"] else None,
        "last_success": run_status["last_success"].strftime("%Y-%m-%d %H:%M:%S") if run_status["last_success"] else None,
        "total_runs": run_status["total_runs"],
        "total_new_courses": run_status["total_new_courses"],
        "total_pushed": run_status["total_pushed"],
        "is_running": run_status["is_running"],
        "last_error": run_status["last_error"],
        "last_daily_summary": run_status["last_daily_summary"].strftime("%Y-%m-%d %H:%M:%S") if run_status["last_daily_summary"] else None,
    }
