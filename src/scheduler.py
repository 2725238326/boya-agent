"""
定时调度模块
使用 APScheduler 定期执行抓取 → 过滤 → 智能推送任务链

推送策略（按距选课开始时间分级）：
  🔴 紧急  (<1h)     → 立即推送
  🟡 近期  (1h~12h)  → 每 3 小时汇总推送
  🟢 从容  (12h~24h) → 每 12 小时汇总推送
  🔵 远期  (>24h)    → 每日汇总推送
"""

import asyncio
import os
import threading
from typing import Any, Dict
from datetime import datetime, timedelta
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from src.models import Course, PushLog, get_session, init_db, FilterConfig
from src.scraper import assess_scrape_health, create_browser_context, scrape_courses, save_courses_to_db
from src.auth import ensure_logged_in
from src.filters import filter_courses, load_filter_config
from src.push.email_push import send_email_notification, send_enroll_reminder_email
from src.push.rss_feed import generate_rss_feed
from src.enroll import auto_enroll_if_enabled
from src.push.telegram_bot import send_status_message, send_reminder_telegram

# 全局调度器实例
scheduler = AsyncIOScheduler()

# 全局运行状态
run_status = {
    "last_run": None,
    "last_success": None,
    "total_runs": 0,
    "total_new_courses": 0,
    "total_pushed": 0,
    "total_push_emails": 0,
    "total_push_courses": 0,
    "is_running": False,
    "last_error": None,
    "last_scrape_health": None,
    "last_daily_summary": None,
}

# 全局浏览器实例（持久化复用）
_browser_state = {
    "pw": None,
    "browser": None,
    "context": None,
    "page": None,
    "scrape_runs": 0,
}

_runtime_loop = None
_active_scrape_task = None
_active_scrape_task_lock = None
_submitted_scrape_future = None
_submitted_scrape_lock = threading.Lock()
BROWSER_MAX_SCRAPE_RUNS = max(5, int(os.getenv("BROWSER_MAX_SCRAPE_RUNS", "25")))

# ── 推送缓冲区 ──────────────────────────────────────────
# 按紧急级别缓冲课程 ID
_push_buffer = {
    "urgent": [],    # 🟡 1h~12h，每 3 小时 flush
    "soon": [],      # 🟢 12h~24h，每 12 小时 flush
}

# 连续失败计数器（用于 Telegram 告警）
_consecutive_failures = 0
_MAX_FAILURES_BEFORE_ALERT = 3
URGENT_DIGEST_MINUTES = max(1, int(os.getenv("PUSH_URGENT_DIGEST_MINUTES", "5")))
SOON_DIGEST_MINUTES = max(5, int(os.getenv("PUSH_SOON_DIGEST_MINUTES", "30")))
ACTIVE_ENROLL_SCRAPE_SECONDS = max(15, int(os.getenv("ACTIVE_ENROLL_SCRAPE_SECONDS", "30")))
ACTIVE_ENROLL_JUST_STARTED_MINUTES = max(5, int(os.getenv("ACTIVE_ENROLL_JUST_STARTED_MINUTES", "15")))
ACTIVE_ENROLL_NOTIFY_MIN_REMAINING = max(3, int(os.getenv("ACTIVE_ENROLL_NOTIFY_MIN_REMAINING", "8")))
HOT_COURSE_REMAINING_THRESHOLD = max(1, int(os.getenv("HOT_COURSE_REMAINING_THRESHOLD", "3")))
HOT_COURSE_STALE_SECONDS = max(30, int(os.getenv("HOT_COURSE_STALE_SECONDS", "90")))


# ═══════════════════════════════════════════════════════
#  浏览器生命周期管理
# ═══════════════════════════════════════════════════════

def set_runtime_loop(loop) -> None:
    global _runtime_loop
    _runtime_loop = loop


def submit_coroutine(coro):
    if _runtime_loop is None:
        raise RuntimeError("scheduler runtime loop is not ready")
    return asyncio.run_coroutine_threadsafe(coro, _runtime_loop)


def queue_scrape_task(mode: str = "full") -> Dict[str, Any]:
    global _submitted_scrape_future

    with _submitted_scrape_lock:
        future = _submitted_scrape_future
        if future and not future.done():
            return {
                "success": True,
                "started": False,
                "joined_existing": True,
                "mode": mode,
            }

        future = submit_coroutine(run_scrape_task(mode=mode))
        _submitted_scrape_future = future

        def _clear_submitted(done_future):
            global _submitted_scrape_future
            with _submitted_scrape_lock:
                if _submitted_scrape_future is done_future:
                    _submitted_scrape_future = None

        future.add_done_callback(_clear_submitted)
        return {
            "success": True,
            "started": True,
            "joined_existing": False,
            "mode": mode,
        }


def _ensure_active_scrape_task_lock():
    global _active_scrape_task_lock
    if _active_scrape_task_lock is None:
        _active_scrape_task_lock = asyncio.Lock()
    return _active_scrape_task_lock


async def _close_browser_local(pw, browser):
    """Close a browser pair without touching global state."""
    try:
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
    except Exception as e:
        logger.warning(f"close browser failed: {e}")


async def close_browser():
    global _browser_state
    await _close_browser_local(_browser_state.get("pw"), _browser_state.get("browser"))
    _browser_state = {"pw": None, "browser": None, "context": None, "page": None, "scrape_runs": 0}


async def _ensure_browser(force_recreate: bool = False):
    """Ensure a healthy Playwright page bound to the main runtime loop."""
    global _browser_state

    page = _browser_state.get("page")
    page_invalid = False
    if page and not force_recreate:
        try:
            _ = page.url
            if _browser_state.get("scrape_runs", 0) < BROWSER_MAX_SCRAPE_RUNS:
                return page
            logger.info(f"browser recycled after {_browser_state['scrape_runs']} runs")
        except Exception:
            page_invalid = True
            logger.warning("browser page became invalid, recreating")

    if force_recreate or page_invalid or page:
        await close_browser()

    logger.info("creating browser instance...")
    try:
        pw, browser, context, page = await create_browser_context()
        _browser_state["pw"] = pw
        _browser_state["browser"] = browser
        _browser_state["context"] = context
        _browser_state["page"] = page
        _browser_state["scrape_runs"] = 0

        logged_in = await ensure_logged_in(page)
        if not logged_in:
            logger.error("login failed")
            await close_browser()
            return None

        return page
    except Exception as e:
        logger.error(f"create browser failed: {e}")
        await close_browser()
        return None


def _mark_browser_used() -> None:
    _browser_state["scrape_runs"] = int(_browser_state.get("scrape_runs", 0)) + 1


def _scrape_result(success: bool, message: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"success": success, "message": message}
    payload.update(extra)
    return payload


# ═══════════════════════════════════════════════════════
#  推送紧急级别分类
# ═══════════════════════════════════════════════════════

def _classify_push_urgency(course):
    """
    根据距离选课开始的时间，将课程分入紧急级别。
    返回: "immediate" | "urgent" | "soon" | "daily"
    """
    now = datetime.now()

    if not course.enroll_start:
        return "daily"  # 无选课时间，归入每日汇总

    delta = course.enroll_start - now
    hours_left = delta.total_seconds() / 3600

    if hours_left <= 0:
        # 选课已经开始
        if course.remaining > 0:
            return "immediate"  # 还有名额，紧急通知
        return "daily"  # 已满，不急

    if hours_left <= 1:
        return "immediate"   # 🔴 <1h → 立即推送
    elif hours_left <= 12:
        return "urgent"      # 🟡 1h~12h → 每 3 小时汇总
    elif hours_left <= 24:
        return "soon"        # 🟢 12h~24h → 每 12 小时汇总
    else:
        return "daily"       # 🔵 >24h → 每日汇总


def _load_active_enrollment_targets(session):
    now = datetime.now()
    return (
        session.query(Course)
        .filter(Course.expired == False)  # noqa: E712
        .filter(Course.enroll_start != None)  # noqa: E711
        .filter(Course.enroll_end != None)  # noqa: E711
        .filter(Course.enroll_start <= now)
        .filter(Course.enroll_end >= now)
        .all()
    )


def _should_push_active_enrollment(course, now: datetime) -> bool:
    if course.remaining <= 0:
        return False
    if not course.enroll_start:
        return course.remaining >= ACTIVE_ENROLL_NOTIFY_MIN_REMAINING

    started_minutes = max(0, (now - course.enroll_start).total_seconds() / 60)
    if started_minutes <= ACTIVE_ENROLL_JUST_STARTED_MINUTES:
        return True

    return course.remaining >= ACTIVE_ENROLL_NOTIFY_MIN_REMAINING


async def _push_active_enrollment_courses(session, config) -> int:
    active_courses = _load_active_enrollment_targets(session)
    now = datetime.now()
    candidates = [course for course in active_courses if _should_push_active_enrollment(course, now)]
    if not candidates:
        return 0

    filtered = filter_courses(candidates, config)
    passed_courses = [course for course, _ in filtered]
    if not passed_courses:
        return 0

    logger.info(
        f"已开选课程巡检推送: {len(passed_courses)} 门 "
        f"(刚开选{ACTIVE_ENROLL_JUST_STARTED_MINUTES}分钟内或剩余名额>={ACTIVE_ENROLL_NOTIFY_MIN_REMAINING})"
    )
    return await _do_push(
        passed_courses,
        config,
        session,
        event_type="new",
        delivery_mode="active_watch",
    )


async def monitor_active_enrollment_courses():
    if run_status["is_running"]:
        return

    session = get_session()
    try:
        active_courses = _load_active_enrollment_targets(session)
        if not active_courses:
            return
        logger.info(f"已开选课程高频巡检: active={len(active_courses)}")
    finally:
        session.close()

    await run_scrape_task(mode="quick")


# ═══════════════════════════════════════════════════════
#  核心抓取任务
# ═══════════════════════════════════════════════════════

async def _run_scrape_task_impl(mode: str = "full"):
    """Run one scrape cycle and return a structured result."""
    global _consecutive_failures

    run_status["is_running"] = True
    run_status["last_run"] = datetime.now()
    run_status["total_runs"] += 1

    pushed_count = 0
    reopened_pushed = 0
    active_pushed = 0
    scraped_count = 0
    new_course_ids = []

    try:
        logger.info("=" * 50)
        logger.info(f"start scrape round {run_status['total_runs']} ({mode})")

        courses_data = None
        last_scrape_error = None
        for attempt in range(2):
            if attempt > 0:
                logger.warning("scrape failed, recreate browser and retry once")
            page = await _ensure_browser(force_recreate=attempt > 0)
            if not page:
                last_scrape_error = "browser/login unavailable"
                continue

            _sync_course_lifecycle()
            try:
                courses_data = await scrape_courses(page, include_details=(mode != "quick"))
                _mark_browser_used()
                break
            except Exception as e:
                last_scrape_error = str(e)
                logger.error(f"scrape attempt {attempt + 1}/2 failed: {e}")
                await close_browser()

        if courses_data is None:
            run_status["last_error"] = last_scrape_error or "scrape failed"
            _consecutive_failures += 1
            await _check_and_alert_failures()
            return _scrape_result(False, run_status["last_error"], scraped_count=0, new_courses=0, mode=mode)

        scraped_count = len(courses_data)
        if not courses_data:
            scrape_health = assess_scrape_health([])
            run_status["last_scrape_health"] = scrape_health
            if not scrape_health["healthy"]:
                msg = (
                    "scrape returned no courses and was blocked as suspicious: "
                    f"db_active={scrape_health['db_active_count']}, "
                    f"min_expected={scrape_health['minimum_expected']}"
                )
                run_status["last_error"] = msg
                logger.error(msg)
                _consecutive_failures += 1
                await _check_and_alert_failures()
                return _scrape_result(False, msg, scraped_count=0, new_courses=0, mode=mode, scrape_health=scrape_health)

            logger.info("no courses scraped")
            run_status["last_success"] = datetime.now()
            run_status["last_error"] = None
            _consecutive_failures = 0
            return _scrape_result(
                True,
                "scrape succeeded but returned no courses",
                scraped_count=0,
                new_courses=0,
                mode=mode,
                scrape_health=scrape_health,
            )

        scrape_health = assess_scrape_health(courses_data)
        run_status["last_scrape_health"] = scrape_health
        if not scrape_health["healthy"]:
            msg = (
                "scrape snapshot blocked as suspiciously sparse: "
                f"scraped={scrape_health['scraped_count']}, "
                f"db_active={scrape_health['db_active_count']}, "
                f"min_expected={scrape_health['minimum_expected']}, "
                f"available={scrape_health['available_count']}, "
                f"preview={scrape_health['preview_count']}"
            )
            run_status["last_error"] = msg
            logger.error(msg)
            _consecutive_failures += 1
            await _check_and_alert_failures()
            return _scrape_result(
                False,
                msg,
                scraped_count=scraped_count,
                new_courses=0,
                mode=mode,
                scrape_health=scrape_health,
            )

        new_course_ids = save_courses_to_db(courses_data)
        _sync_course_lifecycle()
        run_status["total_new_courses"] += len(new_course_ids)

        from src.scraper import _reopened_course_ids
        if _reopened_course_ids:
            session = get_session()
            try:
                reopened = session.query(Course).filter(Course.id.in_(_reopened_course_ids)).all()
                config = load_filter_config()
                if reopened:
                    logger.info(f"found {len(reopened)} reopened courses, push now")
                    reopened_pushed = await _do_push(
                        reopened,
                        config,
                        session,
                        event_type="snipe",
                        delivery_mode="priority",
                    )
                    run_status["total_pushed"] += reopened_pushed
            finally:
                session.close()

        session = get_session()
        try:
            config = load_filter_config()
            active_pushed = await _push_active_enrollment_courses(session, config)
            run_status["total_pushed"] += active_pushed
        finally:
            session.close()

        if not new_course_ids:
            if reopened_pushed or active_pushed:
                msg = f"no new courses, but sent {reopened_pushed + active_pushed} active/reopen alerts"
                logger.info(msg)
            else:
                msg = "no new courses, nothing to push"
                logger.info(msg)
            run_status["last_success"] = datetime.now()
            run_status["last_error"] = None
            _consecutive_failures = 0
            return _scrape_result(
                True,
                msg,
                scraped_count=scraped_count,
                new_courses=0,
                pushed=reopened_pushed + active_pushed,
                mode=mode,
                scrape_health=run_status.get("last_scrape_health"),
            )

        logger.info(f"found {len(new_course_ids)} new courses")

        session = get_session()
        try:
            db_courses = session.query(Course).filter(Course.id.in_(new_course_ids)).all()
            config = load_filter_config()
            filtered = filter_courses(db_courses, config)
            passed_courses = [c for c, _ in filtered]

            if not passed_courses:
                logger.info("new courses found but all were filtered out")
                run_status["last_success"] = datetime.now()
                run_status["last_error"] = None
                _consecutive_failures = 0
                return _scrape_result(
                    True,
                    "scrape succeeded, new courses found, but all were filtered out",
                    scraped_count=scraped_count,
                    new_courses=len(new_course_ids),
                    passed_courses=0,
                    mode=mode,
                    scrape_health=run_status.get("last_scrape_health"),
                )

            logger.info(f"{len(passed_courses)} courses passed filters")
            immediate_courses = []
            for course in passed_courses:
                level = _classify_push_urgency(course)
                logger.info(f"  course [{course.name}] urgency: {level}")
                if level == "immediate":
                    immediate_courses.append(course)
                elif level == "urgent":
                    _push_buffer["urgent"].append(course.id)
                elif level == "soon":
                    _push_buffer["soon"].append(course.id)

            if immediate_courses:
                pushed_count = await _do_push(
                    immediate_courses,
                    config,
                    session,
                    event_type="new",
                    delivery_mode="priority",
                )

            run_status["total_pushed"] += pushed_count
            await auto_enroll_if_enabled(page, passed_courses)
        finally:
            session.close()

        run_status["last_success"] = datetime.now()
        run_status["last_error"] = None
        _consecutive_failures = 0
        logger.info(
            f"??????: ???? {pushed_count} ?, ???? {reopened_pushed} ?, ?????? {active_pushed} ?, "
            f"???: urgent={len(_push_buffer['urgent'])}, soon={len(_push_buffer['soon'])}"
        )
        return _scrape_result(
            True,
            "scrape completed",
            scraped_count=scraped_count,
            new_courses=len(new_course_ids),
            immediate_pushed=pushed_count,
            reopened_pushed=reopened_pushed,
            active_pushed=active_pushed,
            urgent_buffer=len(_push_buffer['urgent']),
            soon_buffer=len(_push_buffer['soon']),
            mode=mode,
            scrape_health=run_status.get("last_scrape_health"),
        )

    except Exception as e:
        run_status["last_error"] = str(e)
        logger.error(f"scrape task failed: {e}")
        _consecutive_failures += 1
        await _check_and_alert_failures()
        return _scrape_result(False, str(e), scraped_count=scraped_count, new_courses=len(new_course_ids), mode=mode)
    finally:
        run_status["is_running"] = False


async def run_scrape_task(mode: str = "full"):
    """Reuse the in-flight scrape task instead of failing concurrent callers."""
    global _active_scrape_task
    joined_existing = False

    async with _ensure_active_scrape_task_lock():
        task = _active_scrape_task
        if task and not task.done():
            logger.info("scrape already in progress, joining existing task")
            joined_existing = True
        else:
            task = asyncio.create_task(_run_scrape_task_impl(mode=mode))
            _active_scrape_task = task
    try:
        result = await task
        if joined_existing and isinstance(result, dict):
            joined = dict(result)
            joined["joined_existing"] = True
            return joined
        return result
    finally:
        async with _ensure_active_scrape_task_lock():
            if _active_scrape_task is task and task.done():
                _active_scrape_task = None


# ═══════════════════════════════════════════════════════
#  推送执行
# ═══════════════════════════════════════════════════════

async def _do_push(courses, config, session, event_type: str = "new", delivery_mode: str = "instant"):
    """执行推送（仅邮件，Telegram 已转为管理员告警专用）"""
    pushed_count = 0

    if config.email_enabled:
        sent_count = await send_email_notification(courses, event_type=event_type, delivery_mode=delivery_mode)
        if sent_count > 0:
            pushed_count += sent_count
            run_status["total_push_emails"] += sent_count
            run_status["total_push_courses"] += len(courses)
            _log_push(courses, "email", len(courses))

    return pushed_count


async def _check_and_alert_failures():
    """连续失败超过阈值时，通过 Telegram 告警管理员"""
    global _consecutive_failures
    if _consecutive_failures >= _MAX_FAILURES_BEFORE_ALERT:
        msg = (
            f"⚠️ 博雅报警：已连续失败 {_consecutive_failures} 次\n"
            f"最后错误: {run_status.get('last_error', '未知')}\n"
            f"上次成功: {run_status.get('last_success', '无')}\n"
            f"请检查服务器状态"
        )
        logger.warning(msg)
        try:
            await send_status_message(msg)
        except Exception as e:
            logger.error(f"Telegram 告警发送失败: {e}")
        # 重置计数器，避免反复告警
        _consecutive_failures = 0


async def flush_push_buffer(buffer_key: str):
    """
    定时刷新推送缓冲区（由调度器调用）
    buffer_key: "urgent" 或 "soon"
    """
    course_ids = list(set(_push_buffer.get(buffer_key, [])))
    _push_buffer[buffer_key] = []

    if not course_ids:
        return

    # 去掉已过期的（是否还需要通知由订阅者级事件去重控制）
    session = get_session()
    try:
        course_ids_to_push = []
        for cid in course_ids:
            c = session.query(Course).filter_by(id=cid).first()
            if c and not c.expired:
                course_ids_to_push.append(cid)
    finally:
        session.close()

    if not course_ids_to_push:
        return

    session = get_session()
    try:
        config = load_filter_config()
        courses = (
            session.query(Course)
            .filter(Course.id.in_(course_ids_to_push))
            .filter(Course.expired == False)  # noqa: E712
            .all()
        )
        if not courses:
            return

        label = "🟡 近期汇总" if buffer_key == "urgent" else "🟢 从容汇总"
        logger.info(f"{label}: 推送 {len(courses)} 门课程")

        pushed_count = await _do_push(
            courses,
            config,
            session,
            event_type="new",
            delivery_mode="digest_urgent" if buffer_key == "urgent" else "digest_soon",
        )
        run_status["total_pushed"] += pushed_count
        logger.info(f"{label}: 完成, 推送 {pushed_count} 条")
    except Exception as e:
        logger.error(f"刷新推送缓冲区失败 [{buffer_key}]: {e}")
    finally:
        session.close()


async def check_urgency_escalation():
    """
    每分钟检查：缓冲区中的课程是否升级到了 🔴 紧急级别
    如果是，立即推送
    """
    now = datetime.now()
    escalated_ids = []

    # 检查 urgent 和 soon 缓冲区
    for key in ["urgent", "soon"]:
        remaining = []
        for cid in _push_buffer.get(key, []):
            session = get_session()
            try:
                course = session.query(Course).filter_by(id=cid).first()
                if not course or course.expired:
                    continue  # 已过期，跳过
                if course.enroll_start:
                    hours_left = (course.enroll_start - now).total_seconds() / 3600
                    if hours_left <= 1:
                        escalated_ids.append(cid)
                        continue  # 升级，不放回缓冲区
                remaining.append(cid)
            finally:
                session.close()
        _push_buffer[key] = remaining

    if not escalated_ids:
        return

    # 立即推送升级的课程
    session = get_session()
    try:
        config = load_filter_config()
        courses = session.query(Course).filter(Course.id.in_(escalated_ids)).all()
        if courses:
            logger.info(f"🔴 紧急升级推送: {len(courses)} 门课程选课即将开始")
            pushed_count = await _do_push(
                courses,
                config,
                session,
                event_type="new",
                delivery_mode="priority",
            )
            run_status["total_pushed"] += pushed_count
    except Exception as e:
        logger.error(f"紧急升级推送失败: {e}")
    finally:
        session.close()


# ═══════════════════════════════════════════════════════
#  每日汇总（保留原有逻辑）
# ═══════════════════════════════════════════════════════

async def run_daily_summary_task():
    """每日汇总推送任务：将未推送且通过过滤的课程汇总后发送"""
    session = get_session()
    try:
        config = load_filter_config()

        pending_courses = (
            session.query(Course)
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

        if config.email_enabled:
            ok = await send_email_notification(
                passed_courses,
                event_type="new",
                delivery_mode="digest_daily",
            )
            if ok:
                pushed_count += len(passed_courses)
                _log_push(passed_courses, "daily_email", len(passed_courses))

        if pushed_count > 0:
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


# ═══════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════

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
                    email_sent = send_enroll_reminder_email(sub.email, course)
                    telegram_sent = await send_reminder_telegram(course)

                    if email_sent:
                        reminder.sent = True
                        logger.info(f"已发送选课提醒: {sub.email} -> {course.name}")
                    else:
                        logger.warning(
                            f"选课提醒邮件发送失败，将保留待重试: {sub.email} -> {course.name}"
                        )

                    if not telegram_sent:
                        logger.debug(f"Telegram 选课提醒未发送: {course.name}")
                except Exception as e:
                    logger.error(f"发送选课提醒失败 {sub.email} -> {course.name}: {e}")
            elif minutes_left <= 0:
                # 已经过了选课时间，标记为已发送
                logger.warning(
                    f"选课提醒已过期未送达，标记为结束: {sub.email} -> {course.name}"
                )
                reminder.sent = True

        session.commit()
    except Exception as e:
        logger.error(f"检查选课提醒出错: {e}")
    finally:
        session.close()


# ═══════════════════════════════════════════════════════
#  调度器管理
# ═══════════════════════════════════════════════════════

def start_scheduler(interval_minutes: int = 3):
    """启动定时调度器"""
    # 抓取任务（默认 3 分钟，因为只是刷新页面）
    scheduler.add_job(
        run_scrape_task,
        trigger=IntervalTrigger(minutes=interval_minutes),
        id="scrape_task",
        replace_existing=True,
        max_instances=1,
    )

    # 选课提醒检查 + 紧急级别升级（每分钟）
    scheduler.add_job(
        check_course_reminders,
        trigger=IntervalTrigger(minutes=1),
        id="course_reminders_task",
        replace_existing=True,
    )
    scheduler.add_job(
        check_urgency_escalation,
        trigger=IntervalTrigger(minutes=1),
        id="urgency_escalation_task",
        replace_existing=True,
    )
    scheduler.add_job(
        monitor_active_enrollment_courses,
        trigger=IntervalTrigger(seconds=ACTIVE_ENROLL_SCRAPE_SECONDS),
        id="active_enrollment_watch_task",
        replace_existing=True,
        max_instances=1,
    )

    # 🟡 近期缓冲区 flush（每 3 小时）
    scheduler.add_job(
        flush_push_buffer,
        args=["urgent"],
        trigger=IntervalTrigger(minutes=URGENT_DIGEST_MINUTES),
        id="flush_urgent_buffer",
        replace_existing=True,
        max_instances=1,
    )

    # 🟢 从容缓冲区 flush（每 12 小时）
    scheduler.add_job(
        flush_push_buffer,
        args=["soon"],
        trigger=IntervalTrigger(minutes=SOON_DIGEST_MINUTES),
        id="flush_soon_buffer",
        replace_existing=True,
        max_instances=1,
    )

    # 🧹 每日清理过期 30 天以上的课程
    scheduler.add_job(
        cleanup_old_courses,
        trigger=CronTrigger(hour=4, minute=0),  # 凌晨 4 点
        id="cleanup_old_courses",
        replace_existing=True,
    )

    _configure_daily_summary_job()
    scheduler.start()
    logger.info(f"定时调度已启动: 抓取间隔={interval_minutes}分钟, "
                f"近期汇总=每{URGENT_DIGEST_MINUTES}分钟, 新课摘要=每{SOON_DIGEST_MINUTES}分钟, "
                f"开选巡检=每{ACTIVE_ENROLL_SCRAPE_SECONDS}秒")


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
    """
    同步课程生命周期：选课结束超过 30 分钟 → 标记为 expired，并从推送缓冲区移除。
    长期清理由 cleanup_old_courses 统一处理，避免已结束课程被立即删库后失去排查上下文。
    """
    session = get_session()
    try:
        now = datetime.now()
        cutoff = now - timedelta(minutes=30)

        ended_courses = (
            session.query(Course)
            .filter(Course.enroll_end != None)  # noqa: E711
            .filter(Course.enroll_end < cutoff)
            .all()
        )

        if not ended_courses:
            return

        ended_ids = {c.id for c in ended_courses}

        for key in _push_buffer:
            _push_buffer[key] = [cid for cid in _push_buffer[key] if cid not in ended_ids]

        newly_expired = 0
        for course in ended_courses:
            if not course.expired:
                newly_expired += 1
            course.expired = True

        session.commit()
        if newly_expired:
            logger.info(f"已标记 {newly_expired} 门选课已结束 30 分钟以上的课程为过期")

    except Exception as e:
        session.rollback()
        logger.error(f"课程生命周期同步失败: {e}")
    finally:
        session.close()

def cleanup_old_courses(max_days: int = 30):
    """清理过期超过 max_days 天的课程"""
    session = get_session()
    try:
        cutoff = datetime.now() - timedelta(days=max_days)
        old_courses = (
            session.query(Course)
            .filter(Course.expired == True)  # noqa: E712
            .filter(Course.enroll_end < cutoff)
            .all()
        )
        count = len(old_courses)
        if count == 0:
            return
        for c in old_courses:
            session.delete(c)
        session.commit()
        logger.info(f"🧹 已清理 {count} 门过期超过 {max_days} 天的课程")
    except Exception as e:
        session.rollback()
        logger.error(f"清理过期课程失败: {e}")
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
        "total_push_emails": run_status["total_push_emails"],
        "total_push_courses": run_status["total_push_courses"],
        "is_running": run_status["is_running"],
        "last_error": run_status["last_error"],
        "last_daily_summary": run_status["last_daily_summary"].strftime("%Y-%m-%d %H:%M:%S") if run_status["last_daily_summary"] else None,
    }
