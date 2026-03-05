"""
Telegram Bot 推送模块
发送课程通知 + 支持交互命令
"""

import os
import asyncio
from datetime import datetime
from typing import List
from loguru import logger

try:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ParseMode
    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    logger.warning("python-telegram-bot 未安装，Telegram 推送不可用")


def get_bot() -> "Bot":
    """获取 Telegram Bot 实例"""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise ValueError("未配置 TELEGRAM_BOT_TOKEN")
    return Bot(token=token)


def get_chat_id() -> str:
    """获取目标 Chat ID"""
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        raise ValueError("未配置 TELEGRAM_CHAT_ID")
    return chat_id


def format_course_message(course) -> str:
    """
    将课程对象格式化为 Telegram 消息（Markdown V2 格式）
    """
    # 签到方式标记（优先用详情页的 check_in_method）
    check_in = getattr(course, 'check_in_method', '') or course.sign_method or '未知'
    sign_icon = "✅" if "自主" in check_in else "⚠️"
    # 名额状态
    if course.remaining > 10:
        cap_icon = "🟢"
    elif course.remaining > 0:
        cap_icon = "🟡"
    else:
        cap_icon = "🔴"

    msg = (
        f"📚 *新博雅课程通知*\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📖 *{_escape_md(course.name)}*\n"
        f"🏷️ 类别: {_escape_md(course.category)}\n"
        f"👨‍🏫 教师: {_escape_md(course.teacher)}\n"
        f"📍 地点: {_escape_md(course.location)}\n"
        f"🏫 校区: {_escape_md(course.campus)}\n"
        f"\n"
        f"⏰ *课程时间*\n"
        f"   开始: {_escape_md(course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知')}\n"
        f"   结束: {_escape_md(course.end_time.strftime('%Y-%m-%d %H:%M') if course.end_time else '未知')}\n"
        f"\n"
        f"📝 *选课信息*\n"
        f"   {sign_icon} 签到方式: {_escape_md(check_in)}\n"
        f"   选课开始: {_escape_md(course.enroll_start.strftime('%Y-%m-%d %H:%M') if course.enroll_start else '未知')}\n"
        f"   选课截止: {_escape_md(course.enroll_end.strftime('%Y-%m-%d %H:%M') if course.enroll_end else '未知')}\n"
        f"   {cap_icon} 名额: {course.enrolled}/{course.capacity} \\(剩余 {course.remaining}\\)\n"
    )
    return msg


def _escape_md(text: str) -> str:
    """转义 Telegram MarkdownV2 特殊字符"""
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#',
                     '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text


async def send_course_notification(course, include_enroll_button: bool = False) -> bool:
    """
    发送单条课程通知到 Telegram

    Args:
        course: Course 对象
        include_enroll_button: 是否包含选课按钮

    Returns:
        是否发送成功
    """
    if not HAS_TELEGRAM:
        logger.error("Telegram 模块未安装")
        return False

    try:
        bot = get_bot()
        chat_id = get_chat_id()
        message = format_course_message(course)

        # 构建 inline 键盘
        keyboard = []
        if include_enroll_button and course.is_enrollable:
            keyboard.append([
                InlineKeyboardButton("🎯 一键选课", callback_data=f"enroll_{course.id}"),
            ])
        keyboard.append([
            InlineKeyboardButton("🔍 查看详情", url=f"https://bykc.buaa.edu.cn/system/course-select"),
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )
        logger.info(f"Telegram 推送成功: {course.name}")
        return True

    except Exception as e:
        logger.error(f"Telegram 推送失败: {e}")
        return False


async def send_batch_notifications(courses: list) -> int:
    """
    批量发送课程通知

    Args:
        courses: Course 对象列表

    Returns:
        成功发送的数量
    """
    success_count = 0
    for course in courses:
        ok = await send_course_notification(course, include_enroll_button=True)
        if ok:
            success_count += 1
        # Telegram API 限频：每秒最多 1 条
        await asyncio.sleep(1.5)
    return success_count


async def send_daily_summary_notification(courses: list) -> bool:
    """
    发送每日汇总通知（单条或分段）
    """
    if not HAS_TELEGRAM:
        logger.error("Telegram 模块未安装")
        return False

    if not courses:
        return True

    try:
        bot = get_bot()
        chat_id = get_chat_id()
        now_text = datetime.now().strftime("%Y-%m-%d")

        lines = [
            "🗓️ *博雅课程每日汇总*",
            f"日期: {_escape_md(now_text)}",
            f"共 {len(courses)} 门符合条件课程",
            "━━━━━━━━━━━━━━━",
        ]

        for i, course in enumerate(courses, start=1):
            check_in = getattr(course, "check_in_method", "") or course.sign_method or "未知"
            enroll_end = course.enroll_end.strftime('%Y-%m-%d %H:%M') if course.enroll_end else "未知"
            lines.append(
                f"{i}\\. *{_escape_md(course.name)}* | {_escape_md(course.category)}\n"
                f"   签到: {_escape_md(check_in)} | 剩余: {course.remaining}\n"
                f"   截止: {_escape_md(enroll_end)}"
            )

        full_text = "\n".join(lines)
        chunks = _split_message(full_text, max_len=3500)
        for chunk in chunks:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
            await asyncio.sleep(1.0)

        logger.info(f"Telegram 每日汇总推送成功: {len(courses)} 门课程")
        return True
    except Exception as e:
        logger.error(f"Telegram 每日汇总推送失败: {e}")
        return False


def _split_message(text: str, max_len: int = 3500) -> List[str]:
    """按行切分长消息，避免超过 Telegram 文本长度限制"""
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1
        if current and current_len + line_len > max_len:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


async def send_enroll_confirmation(course) -> bool:
    """发送选课确认提醒"""
    if not HAS_TELEGRAM:
        return False

    try:
        bot = get_bot()
        chat_id = get_chat_id()

        msg = (
            f"🔔 *自动选课确认*\n\n"
            f"即将为您选课:\n"
            f"📖 *{_escape_md(course.name)}*\n"
            f"⏰ {_escape_md(course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知')}\n"
            f"📍 {_escape_md(course.location)}\n\n"
            f"如需取消，请在 Web 控制台关闭自动选课开关。"
        )

        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return True
    except Exception as e:
        logger.error(f"发送选课确认失败: {e}")
        return False



async def send_enroll_confirmation(course) -> bool:
    """发送选课确认提醒"""
    if not HAS_TELEGRAM:
        return False

    try:
        bot = get_bot()
        chat_id = get_chat_id()

        msg = (
            f"🔔 *自动选课确认*\n\n"
            f"即将为您选课:\n"
            f"📖 *{_escape_md(course.name)}*\n"
            f"⏰ {_escape_md(course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知')}\n"
            f"📍 {_escape_md(course.location)}\n\n"
            f"如需取消，请在 Web 控制台关闭自动选课开关。"
        )

        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return True
    except Exception as e:
        logger.error(f"发送选课确认失败: {e}")
        return False


async def send_enroll_result(course, success: bool, message: str = "") -> bool:
    """发送选课结果通知"""
    if not HAS_TELEGRAM:
        return False

    try:
        bot = get_bot()
        chat_id = get_chat_id()

        icon = "✅" if success else "❌"
        status = "成功" if success else "失败"

        msg = (
            f"{icon} *选课{_escape_md(status)}*\n\n"
            f"📖 {_escape_md(course.name)}\n"
        )
        if message:
            msg += f"📝 {_escape_md(message)}\n"

        await bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return True
    except Exception as e:
        logger.error(f"发送选课结果失败: {e}")
        return False


async def send_status_message(text: str) -> bool:
    """发送普通状态消息"""
    if not HAS_TELEGRAM:
        return False

    try:
        bot = get_bot()
        chat_id = get_chat_id()
        await bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception as e:
        logger.error(f"Telegram 消息发送失败: {e}")
        return False


async def send_reminder_telegram(course) -> bool:
    """通过 Telegram 发送选课即将开始的提醒"""
    try:
        import aiohttp
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return False

        enroll_str = course.enroll_start.strftime('%Y-%m-%d %H:%M') if course.enroll_start else '即将'
        text = (
            f"⏰ <b>选课即将开始提醒</b>\n\n"
            f"<b>{course.name}</b>\n"
            f"{course.category} · {course.teacher}\n"
            f"选课开始：<b>{enroll_str}</b>\n\n"
            f"请立即打开博雅选课系统准备选课"
        )
        proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
            }, proxy=proxy) as resp:
                return resp.status == 200
    except Exception as e:
        logger.warning(f"Telegram 选课提醒发送失败: {e}")
        return False
