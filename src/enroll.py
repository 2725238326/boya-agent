"""
自动选课模块
使用 Playwright 在选课窗口开启时自动点击选课按钮
默认关闭，需通过 Web 控制台开启
"""

from datetime import datetime
from loguru import logger
from playwright.async_api import Page

from src.models import Course, EnrollLog, FilterConfig, get_session
from src.filters import get_auto_enroll_candidates, load_filter_config


async def attempt_enroll(page: Page, course: Course) -> tuple:
    """
    尝试选课

    Args:
        page: 已登录的 Playwright page 对象
        course: 目标课程

    Returns:
        (是否成功, 消息)
    """
    try:
        logger.info(f"尝试自动选课: {course.name}")

        # 导航到选课页面
        await page.goto(
            "https://bykc.buaa.edu.cn/system/course-select",
            wait_until="networkidle",
            timeout=30000,
        )
        await page.wait_for_selector("table", timeout=15000)

        # 查找目标课程行
        rows = await page.query_selector_all("table tbody tr")
        target_row = None

        for row in rows:
            text = await row.inner_text()
            if course.name in text:
                target_row = row
                break

        if not target_row:
            # 可能在其他页面，尝试翻页查找
            logger.warning(f"当前页未找到课程: {course.name}")
            return False, "在当前页面未找到该课程"

        # 查找选课 / 详细介绍按钮
        enroll_btn = await target_row.query_selector(
            'button:has-text("选课"), '
            'a:has-text("选课"), '
            'button:has-text("订阅"), '
            'a:has-text("订阅")'
        )

        if not enroll_btn:
            return False, "未找到选课按钮，可能已选或已满"

        # 检查按钮是否可点击
        is_disabled = await enroll_btn.is_disabled()
        if is_disabled:
            return False, "选课按钮不可用"

        # 点击选课
        await enroll_btn.click()

        # 等待确认对话框（如果有）
        try:
            confirm_btn = page.locator(
                'button:has-text("确认"), button:has-text("确定"), '
                '.ant-modal-confirm-btns button.ant-btn-primary'
            )
            await confirm_btn.first.click(timeout=5000)
        except Exception:
            pass  # 可能没有确认对话框

        # 等待结果
        await page.wait_for_timeout(3000)

        # 检查是否出现成功提示
        try:
            success_msg = await page.query_selector(
                '.ant-message-success, '
                '.ant-notification-notice-success, '
                ':text("选课成功"), :text("成功")'
            )
            if success_msg:
                logger.info(f"选课成功: {course.name}")
                return True, "选课成功"
        except Exception:
            pass

        # 检查是否出现错误提示
        try:
            error_msg = await page.query_selector(
                '.ant-message-error, '
                '.ant-notification-notice-error'
            )
            if error_msg:
                error_text = await error_msg.inner_text()
                logger.warning(f"选课失败: {error_text}")
                return False, f"选课失败: {error_text}"
        except Exception:
            pass

        return True, "已提交选课请求，请手动确认结果"

    except Exception as e:
        logger.error(f"选课操作异常: {e}")
        return False, f"操作异常: {str(e)}"


def log_enroll_attempt(course_id: str, course_name: str, success: bool, message: str):
    """记录选课操作日志"""
    session = get_session()
    try:
        log = EnrollLog(
            course_id=course_id,
            course_name=course_name,
            attempted_at=datetime.now(),
            success=success,
            message=message,
        )
        session.add(log)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"记录选课日志失败: {e}")
    finally:
        session.close()


def get_today_enroll_count() -> int:
    """获取今天已自动选课的次数"""
    session = get_session()
    try:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        count = (
            session.query(EnrollLog)
            .filter(EnrollLog.attempted_at >= today, EnrollLog.success == True)
            .count()
        )
        return count
    finally:
        session.close()


async def auto_enroll_if_enabled(page: Page, filtered_courses: list):
    """
    如果自动选课已启用，尝试选课

    Args:
        page: Playwright page 对象
        filtered_courses: 已通过过滤的课程列表
    """
    config = load_filter_config()

    if not config.auto_enroll_enabled:
        return

    # 检查今日选课次数
    today_count = get_today_enroll_count()
    if today_count >= config.max_auto_enroll_per_day:
        logger.info(f"今日已自动选课 {today_count} 次，达到上限 {config.max_auto_enroll_per_day}")
        return

    # 获取候选课程
    candidates = get_auto_enroll_candidates(filtered_courses, config)
    if not candidates:
        logger.info("无符合自动选课条件的课程")
        return

    remaining_slots = config.max_auto_enroll_per_day - today_count

    for course in candidates[:remaining_slots]:
        # 发送确认提醒（如果开启）
        if config.confirm_before_enroll:
            from src.push.telegram_bot import send_enroll_confirmation
            await send_enroll_confirmation(course)

        # 执行选课
        success, message = await attempt_enroll(page, course)

        # 记录日志
        log_enroll_attempt(course.id, course.name, success, message)

        # 发送结果通知
        from src.push.telegram_bot import send_enroll_result
        from src.push.email_push import send_enroll_result_email
        await send_enroll_result(course, success, message)
        await send_enroll_result_email(course, success, message)

        # 更新数据库
        if success:
            session = get_session()
            try:
                db_course = session.query(Course).filter_by(id=course.id).first()
                if db_course:
                    db_course.enrolled_by_bot = True
                    session.commit()
            finally:
                session.close()

        logger.info(f"自动选课 {'成功' if success else '失败'}: {course.name} - {message}")
