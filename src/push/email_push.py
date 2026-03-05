"""
邮件推送模块
支持 Gmail SMTP 发送 HTML 格式课程通知邮件
支持多订阅者按个人偏好过滤推送
"""

import os
import ssl
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List
from loguru import logger


def _get_smtp_config() -> dict:
    """获取 SMTP 配置（支持 Gmail）"""
    return {
        "server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() == "true",
        "username": os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
    }


def _send_raw_email(to_email: str, subject: str, html: str) -> bool:
    """底层发邮件函数"""
    config = _get_smtp_config()
    if not config["username"] or not config["password"]:
        logger.error("未配置 SMTP 账号/密码")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config["username"]
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html", "utf-8"))

        if config["use_tls"]:
            # Gmail: STARTTLS on port 587
            server = smtplib.SMTP(config["server"], config["port"], timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config["username"], config["password"])
            server.send_message(msg)
            server.quit()
        else:
            # SSL on port 465 (QQ etc.)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(config["server"], config["port"], context=context, timeout=10) as server:
                server.login(config["username"], config["password"])
                server.send_message(msg)

        return True
    except Exception as e:
        logger.error(f"邮件发送失败 [{to_email}]: {e}")
        return False


# ========== 通用样式 ==========

_EMAIL_ACCENT = "#0071e3"  # Apple blue
_EMAIL_BG = "#f5f5f7"
_EMAIL_CARD_BG = "#ffffff"
_EMAIL_TEXT = "#1d1d1f"
_EMAIL_MUTED = "#86868b"


def _email_shell(title: str, body_html: str, footer_html: str = "") -> str:
    """统一的邮件外壳模板 — 移动端 & 桌面端双适配"""
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
</head>
<body style="margin:0; padding:0; background:{_EMAIL_BG};
             font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Roboto,Helvetica,Arial,sans-serif;
             -webkit-font-smoothing:antialiased; color:{_EMAIL_TEXT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_EMAIL_BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px; background:{_EMAIL_CARD_BG};
       border-radius:20px; overflow:hidden; box-shadow:0 2px 16px rgba(0,0,0,0.06);">
<!-- Header -->
<tr><td style="background:{_EMAIL_ACCENT}; padding:28px 24px; text-align:center;">
  <h1 style="margin:0; color:#fff; font-size:22px; font-weight:700; letter-spacing:-0.01em;">{title}</h1>
</td></tr>
<!-- Body -->
<tr><td style="padding:24px;">
{body_html}
</td></tr>
<!-- Footer -->
<tr><td style="padding:16px 24px; border-top:1px solid #f0f0f0; text-align:center; font-size:12px; color:{_EMAIL_MUTED};">
  BUAA 博雅课程推送{footer_html}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


# ========== 验证邮件 ==========

def send_verification_email(to_email: str, verify_url: str) -> bool:
    """发送邮箱验证邮件"""
    body = f"""
<p style="font-size:15px; color:{_EMAIL_TEXT}; line-height:1.6; text-align:center; margin:0 0 24px;">
  点击下方按钮验证邮箱，即可开始接收博雅课程推送通知
</p>
<table role="presentation" width="100%"><tr><td align="center">
  <a href="{verify_url}" style="display:inline-block; padding:14px 40px; background:{_EMAIL_ACCENT};
     color:#fff; text-decoration:none; border-radius:12px; font-weight:600; font-size:16px;">
    验证邮箱
  </a>
</td></tr></table>
<p style="font-size:12px; color:{_EMAIL_MUTED}; text-align:center; margin:20px 0 0; word-break:break-all;">
  如按钮无法点击，请复制链接：<br>{verify_url}
</p>"""
    html = _email_shell("验证你的邮箱", body)
    ok = _send_raw_email(to_email, "验证你的博雅课程推送订阅", html)
    if ok:
        logger.info(f"验证邮件已发送: {to_email}")
    return ok


# ========== 课程通知 ==========


def _build_course_html(course, remind_url: str = "") -> str:
    """构建单条课程 HTML 卡片 — 移动端友好的单列布局"""
    check_in = getattr(course, 'check_in_method', '') or getattr(course, 'sign_method', '') or ''
    is_self_sign = "自主" in check_in
    sign_color = "#34c759" if is_self_sign else "#ff9500"
    remaining = course.remaining
    cap_color = "#34c759" if remaining > 10 else ("#ff9500" if remaining > 0 else "#ff3b30")

    enroll_start_str = course.enroll_start.strftime('%m/%d %H:%M') if course.enroll_start else '未知'
    start_str = course.start_time.strftime('%m/%d %H:%M') if course.start_time else '未知'

    remind_btn = ""
    if remind_url:
        remind_btn = f"""
<table role="presentation" width="100%" style="margin-top:14px;"><tr><td align="center">
  <a href="{remind_url}" style="display:inline-block; padding:10px 24px; background:#f5f5f7;
     color:{_EMAIL_ACCENT}; text-decoration:none; border-radius:10px; font-weight:600; font-size:13px;
     border:1px solid #e5e5ea;">提醒我选课</a>
</td></tr></table>"""

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin:0 0 16px; border:1px solid #f0f0f0; border-radius:16px; overflow:hidden;">
<tr><td style="padding:20px;">
  <p style="margin:0 0 4px; font-size:17px; font-weight:600; color:{_EMAIL_TEXT}; line-height:1.4;">
    {course.name}
  </p>
  <p style="margin:0 0 14px; font-size:13px; color:{_EMAIL_MUTED};">
    {course.category} · {course.teacher} · {course.campus}
  </p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px; color:{_EMAIL_TEXT};">
    <tr>
      <td style="padding:5px 0; width:50%;"><span style="color:{_EMAIL_MUTED}">地点</span><br>{course.location}</td>
      <td style="padding:5px 0;"><span style="color:{_EMAIL_MUTED}">签到方式</span><br><span style="color:{sign_color}; font-weight:600;">{check_in or '直接选课'}</span></td>
    </tr>
    <tr>
      <td style="padding:5px 0;"><span style="color:{_EMAIL_MUTED}">课程时间</span><br>{start_str}</td>
      <td style="padding:5px 0;"><span style="color:{_EMAIL_MUTED}">名额</span><br><span style="color:{cap_color}; font-weight:600;">{course.enrolled}/{course.capacity} (剩余 {remaining})</span></td>
    </tr>
    <tr>
      <td colspan="2" style="padding:5px 0;"><span style="color:{_EMAIL_MUTED}">选课开始</span><br>{enroll_start_str}</td>
    </tr>
  </table>
  {remind_btn}
</td></tr>
</table>"""


def _build_notification_html(courses: list, unsubscribe_url: str = "", sub_token: str = "", base_url: str = "") -> str:
    """构建完整通知邮件"""
    cards = []
    for c in courses:
        remind_url = f"{base_url}/api/remind/{sub_token}/{c.id}" if sub_token and base_url else ""
        cards.append(_build_course_html(c, remind_url))

    cards_html = "\n".join(cards)
    subtitle = f'<p style="margin:6px 0 0; color:rgba(255,255,255,0.75); font-size:14px;">发现 {len(courses)} 门符合条件的新课程</p>'

    unsub_link = ""
    if unsubscribe_url:
        unsub_link = f' · <a href="{unsubscribe_url}" style="color:{_EMAIL_ACCENT};">退订</a>'

    body = f"{subtitle if len(courses) > 0 else ''}</td></tr><tr><td style='padding:24px;'>{cards_html}"
    footer = unsub_link

    # We inline the shell manually here for the subtitle in the header
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin:0; padding:0; background:{_EMAIL_BG};
             font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Roboto,Helvetica,Arial,sans-serif;
             -webkit-font-smoothing:antialiased; color:{_EMAIL_TEXT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{_EMAIL_BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px; background:{_EMAIL_CARD_BG};
       border-radius:20px; overflow:hidden; box-shadow:0 2px 16px rgba(0,0,0,0.06);">
<tr><td style="background:{_EMAIL_ACCENT}; padding:28px 24px; text-align:center;">
  <h1 style="margin:0; color:#fff; font-size:22px; font-weight:700;">博雅课程新通知</h1>
  <p style="margin:6px 0 0; color:rgba(255,255,255,0.75); font-size:14px;">发现 {len(courses)} 门符合条件的新课程</p>
</td></tr>
<tr><td style="padding:24px;">
{cards_html}
</td></tr>
<tr><td style="padding:16px 24px; border-top:1px solid #f0f0f0; text-align:center; font-size:12px; color:{_EMAIL_MUTED};">
  BUAA 博雅课程推送{unsub_link}
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def _filter_for_subscriber(courses: list, sub) -> list:
    """根据订阅者偏好过滤课程"""
    result = []
    for c in courses:
        # 校区过滤
        if sub.campus_filter and sub.campus_filter not in (c.campus or ""):
            continue
        # 自主签到过滤
        if sub.self_sign_only:
            check_in = getattr(c, 'check_in_method', '') or ''
            if "自主" not in check_in:
                continue
        # 类别过滤
        sub_cats = sub.categories
        if sub_cats and c.category not in sub_cats:
            continue
        result.append(c)
    return result


async def send_email_to_subscribers(courses: list, base_url: str = "") -> int:
    """
    向所有活跃且已验证的订阅者发送课程通知
    每个订阅者按自己的偏好独立过滤
    返回成功发送数
    """
    from src.models import EmailSubscriber, get_session

    session = get_session()
    try:
        subs = (
            session.query(EmailSubscriber)
            .filter_by(verified=True, active=True)
            .all()
        )
        if not subs:
            logger.info("没有活跃的邮件订阅者")
            return 0

        sent_count = 0
        for sub in subs:
            filtered = _filter_for_subscriber(courses, sub)
            if not filtered:
                continue

            unsub_url = f"{base_url}/api/unsubscribe/{sub.token}" if base_url else ""
            html = _build_notification_html(filtered, unsub_url, sub_token=sub.token, base_url=base_url)
            ok = _send_raw_email(sub.email, f"博雅新课程通知 ({len(filtered)} 门)", html)
            if ok:
                sent_count += 1
                logger.info(f"邮件推送成功: {len(filtered)} 门课程 -> {sub.email}")
            else:
                logger.warning(f"邮件推送失败: {sub.email}")

        return sent_count
    finally:
        session.close()


# ========== 兼容旧接口 ==========

async def send_email_notification(courses: list) -> bool:
    """旧接口兼容：向所有订阅者推送"""
    count = await send_email_to_subscribers(courses)
    return count > 0


async def send_enroll_result_email(course, success: bool, message: str = "") -> bool:
    """发送选课结果邮件（发给所有订阅者）"""
    from src.models import EmailSubscriber, get_session

    session = get_session()
    try:
        subs = (
            session.query(EmailSubscriber)
            .filter_by(verified=True, active=True)
            .all()
        )

        status_label = "成功" if success else "失败"
        body = f"""
<p style="font-size:15px; line-height:1.6; margin:0 0 12px;">
  <strong>课程：</strong>{course.name}<br>
  <strong>时间：</strong>{course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}<br>
  <strong>地点：</strong>{course.location}
</p>
{f'<p style="font-size:14px; color:{_EMAIL_MUTED};">备注：{message}</p>' if message else ''}"""
        html = _email_shell(f"自动选课{status_label}", body)

        sent = 0
        for sub in subs:
            if _send_raw_email(sub.email, f"选课{status_label}: {course.name}", html):
                sent += 1
        return sent > 0
    finally:
        session.close()


# ========== 选课提醒 ==========

def send_enroll_reminder_email(to_email: str, course) -> bool:
    """发送选课即将开始提醒"""
    enroll_str = course.enroll_start.strftime('%Y-%m-%d %H:%M') if course.enroll_start else '即将'
    body = f"""
<p style="font-size:16px; font-weight:600; color:{_EMAIL_TEXT}; margin:0 0 8px;">
  {course.name}
</p>
<p style="font-size:14px; color:{_EMAIL_MUTED}; margin:0 0 20px;">
  {course.category} · {course.teacher} · {course.campus}
</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f5f5f7; border-radius:12px; margin-bottom:20px;">
<tr><td style="padding:16px; text-align:center;">
  <p style="margin:0; font-size:14px; color:{_EMAIL_MUTED};">选课开始时间</p>
  <p style="margin:4px 0 0; font-size:24px; font-weight:700; color:{_EMAIL_ACCENT};">{enroll_str}</p>
</td></tr></table>
<p style="font-size:14px; color:{_EMAIL_TEXT}; text-align:center; margin:0;">
  请提前打开博雅选课系统准备选课
</p>"""
    html = _email_shell("选课即将开始", body)
    return _send_raw_email(to_email, f"选课提醒：{course.name}", html)


async def send_reminder_telegram(course) -> bool:
    """通过 Telegram 发送选课即将开始的提醒"""
    try:
        from src.push.telegram_bot import send_batch_notifications
        # We exploit the existing notification function
        # but prefix with a reminder tag
        import os
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return False

        import aiohttp
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
