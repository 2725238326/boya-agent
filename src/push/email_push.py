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
            server = smtplib.SMTP(config["server"], config["port"])
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config["username"], config["password"])
            server.send_message(msg)
            server.quit()
        else:
            # SSL on port 465 (QQ etc.)
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(config["server"], config["port"], context=context) as server:
                server.login(config["username"], config["password"])
                server.send_message(msg)

        return True
    except Exception as e:
        logger.error(f"邮件发送失败 [{to_email}]: {e}")
        return False


# ========== 验证邮件 ==========

def send_verification_email(to_email: str, verify_url: str) -> bool:
    """发送邮箱验证邮件"""
    html = f"""
    <html>
    <body style="font-family: -apple-system, sans-serif; background:#f5f5f5; padding:20px; margin:0;">
        <div style="max-width:500px; margin:0 auto; background:#fff; border-radius:16px; overflow:hidden;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
            <div style="background:linear-gradient(135deg, #667eea, #764ba2); padding:30px; text-align:center;">
                <h1 style="color:#fff; margin:0; font-size:22px;">🎓 验证你的邮箱</h1>
            </div>
            <div style="padding:30px; text-align:center;">
                <p style="color:#555; font-size:15px; margin-bottom:24px;">
                    点击下方按钮验证邮箱，即可开始接收博雅课程推送通知
                </p>
                <a href="{verify_url}"
                   style="display:inline-block; padding:14px 36px; background:linear-gradient(135deg,#667eea,#764ba2);
                          color:#fff; text-decoration:none; border-radius:10px; font-weight:bold; font-size:15px;">
                    ✅ 验证邮箱
                </a>
                <p style="color:#999; font-size:12px; margin-top:20px;">
                    如果按钮无法点击，请复制以下链接到浏览器：<br>
                    <span style="color:#667eea;">{verify_url}</span>
                </p>
            </div>
            <div style="background:#f9f9f9; padding:12px; text-align:center; font-size:11px; color:#bbb;">
                由 BUAA 博雅课程推送智能体发送
            </div>
        </div>
    </body>
    </html>
    """
    ok = _send_raw_email(to_email, "🎓 验证你的博雅课程推送订阅", html)
    if ok:
        logger.info(f"验证邮件已发送: {to_email}")
    return ok


# ========== 课程通知 ==========

def _build_course_html(course) -> str:
    """构建单条课程 HTML 卡片"""
    check_in = getattr(course, 'check_in_method', '') or getattr(course, 'sign_method', '') or ''
    sign_color = "#4CAF50" if "自主" in check_in else "#FF9800"
    remaining = course.remaining
    cap_color = "#4CAF50" if remaining > 10 else ("#FF9800" if remaining > 0 else "#F44336")

    return f"""
    <div style="border:1px solid #e0e0e0; border-radius:12px; padding:20px; margin:15px 0;
                background:linear-gradient(135deg, #667eea11, #764ba211);
                box-shadow: 0 2px 8px rgba(0,0,0,0.08);">
        <h3 style="margin:0 0 12px 0; color:#333; font-size:18px;">
            📖 {course.name}
        </h3>
        <table style="width:100%; border-collapse:collapse; font-size:14px; color:#555;">
            <tr>
                <td style="padding:6px 0;"><strong>🏷️ 类别</strong></td>
                <td>{course.category}</td>
                <td style="padding:6px 0;"><strong>👨‍🏫 教师</strong></td>
                <td>{course.teacher}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;"><strong>📍 地点</strong></td>
                <td>{course.location}</td>
                <td style="padding:6px 0;"><strong>🏫 校区</strong></td>
                <td>{course.campus}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;"><strong>⏰ 课程</strong></td>
                <td>{course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}</td>
                <td style="padding:6px 0;"><strong>⏰ 结束</strong></td>
                <td>{course.end_time.strftime('%Y-%m-%d %H:%M') if course.end_time else '未知'}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;"><strong>✍️ 签到</strong></td>
                <td><span style="color:{sign_color}; font-weight:bold;">{check_in or '直接选课'}</span></td>
                <td style="padding:6px 0;"><strong>👥 名额</strong></td>
                <td><span style="color:{cap_color}; font-weight:bold;">{course.enrolled}/{course.capacity} (剩余 {remaining})</span></td>
            </tr>
        </table>
    </div>
    """


def _build_notification_html(courses: list, unsubscribe_url: str = "") -> str:
    """构建完整通知邮件"""
    cards = "\n".join(_build_course_html(c) for c in courses)
    unsub_link = ""
    if unsubscribe_url:
        unsub_link = f' | <a href="{unsubscribe_url}" style="color:#667eea;">退订</a>'

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                 background:#f5f5f5; padding:20px; margin:0;">
        <div style="max-width:700px; margin:0 auto; background:#fff;
                    border-radius:16px; overflow:hidden;
                    box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
            <div style="background:linear-gradient(135deg, #667eea, #764ba2);
                        padding:30px; text-align:center;">
                <h1 style="color:#fff; margin:0; font-size:24px;">
                    🎓 博雅课程新通知
                </h1>
                <p style="color:rgba(255,255,255,0.8); margin:8px 0 0 0; font-size:14px;">
                    共发现 {len(courses)} 门符合条件的新课程
                </p>
            </div>
            <div style="padding:20px;">
                {cards}
            </div>
            <div style="background:#f9f9f9; padding:15px; text-align:center;
                        border-top:1px solid #eee; font-size:12px; color:#999;">
                由 BUAA 博雅课程推送智能体自动发送{unsub_link}
            </div>
        </div>
    </body>
    </html>
    """


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
            html = _build_notification_html(filtered, unsub_url)
            ok = _send_raw_email(sub.email, f"📚 博雅新课程通知 ({len(filtered)} 门)", html)
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

        status = "成功 ✅" if success else "失败 ❌"
        html = f"""
        <html><body style="font-family: sans-serif; padding:20px;">
        <h2>{'✅' if success else '❌'} 自动选课{status}</h2>
        <p><strong>课程：</strong>{course.name}</p>
        <p><strong>时间：</strong>{course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}</p>
        <p><strong>地点：</strong>{course.location}</p>
        {"<p><strong>备注：</strong>" + message + "</p>" if message else ""}
        </body></html>
        """

        sent = 0
        for sub in subs:
            if _send_raw_email(sub.email, f"选课{status}: {course.name}", html):
                sent += 1
        return sent > 0
    finally:
        session.close()
