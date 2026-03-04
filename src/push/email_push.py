"""
邮件推送模块
通过 SMTP 发送 HTML 格式的课程通知邮件
"""

import os
import ssl
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List
from loguru import logger


def _get_smtp_config() -> dict:
    """获取 SMTP 配置"""
    return {
        "server": os.getenv("SMTP_SERVER", "smtp.qq.com"),
        "port": int(os.getenv("SMTP_PORT", "465")),
        "username": os.getenv("SMTP_USERNAME", ""),
        "password": os.getenv("SMTP_PASSWORD", ""),
        "receiver": os.getenv("EMAIL_RECEIVER", ""),
    }


def _build_course_html(course) -> str:
    """构建单条课程的 HTML 卡片"""
    sign_color = "#4CAF50" if "自主" in course.sign_method else "#FF9800"
    remaining = course.remaining
    if remaining > 10:
        cap_color = "#4CAF50"
    elif remaining > 0:
        cap_color = "#FF9800"
    else:
        cap_color = "#F44336"

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
                <td style="padding:6px 0;"><strong>⏰ 课程开始</strong></td>
                <td>{course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}</td>
                <td style="padding:6px 0;"><strong>⏰ 课程结束</strong></td>
                <td>{course.end_time.strftime('%Y-%m-%d %H:%M') if course.end_time else '未知'}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;"><strong>📝 选课开始</strong></td>
                <td>{course.enroll_start.strftime('%Y-%m-%d %H:%M') if course.enroll_start else '未知'}</td>
                <td style="padding:6px 0;"><strong>📝 选课截止</strong></td>
                <td>{course.enroll_end.strftime('%Y-%m-%d %H:%M') if course.enroll_end else '未知'}</td>
            </tr>
            <tr>
                <td style="padding:6px 0;"><strong>✍️ 签到方式</strong></td>
                <td><span style="color:{sign_color}; font-weight:bold;">{course.sign_method}</span></td>
                <td style="padding:6px 0;"><strong>👥 名额</strong></td>
                <td><span style="color:{cap_color}; font-weight:bold;">{course.enrolled}/{course.capacity} (剩余 {remaining})</span></td>
            </tr>
        </table>
    </div>
    """


def _build_email_html(courses: list) -> str:
    """构建完整的通知邮件 HTML"""
    cards = "\n".join(_build_course_html(c) for c in courses)

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
            <!-- Header -->
            <div style="background:linear-gradient(135deg, #667eea, #764ba2);
                        padding:30px; text-align:center;">
                <h1 style="color:#fff; margin:0; font-size:24px;">
                    🎓 博雅课程新通知
                </h1>
                <p style="color:rgba(255,255,255,0.8); margin:8px 0 0 0; font-size:14px;">
                    共发现 {len(courses)} 门符合条件的新课程
                </p>
            </div>

            <!-- Content -->
            <div style="padding:20px;">
                {cards}
            </div>

            <!-- Footer -->
            <div style="background:#f9f9f9; padding:15px; text-align:center;
                        border-top:1px solid #eee; font-size:12px; color:#999;">
                由 BUAA 博雅课程推送智能体自动发送 |
                <a href="https://bykc.buaa.edu.cn/system/course-select"
                   style="color:#667eea;">前往选课</a>
            </div>
        </div>
    </body>
    </html>
    """


async def send_email_notification(courses: list) -> bool:
    """
    发送课程通知邮件

    Args:
        courses: Course 对象列表

    Returns:
        是否发送成功
    """
    config = _get_smtp_config()

    if not config["username"] or not config["password"]:
        logger.error("未配置 SMTP 账号/密码")
        return False

    if not config["receiver"]:
        logger.error("未配置收件人地址")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📚 博雅新课程通知 ({len(courses)} 门课程)"
        msg["From"] = config["username"]
        msg["To"] = config["receiver"]

        html_content = _build_email_html(courses)
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        # 使用 SSL 连接
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config["server"], config["port"], context=context) as server:
            server.login(config["username"], config["password"])
            server.send_message(msg)

        logger.info(f"邮件推送成功: {len(courses)} 门课程 -> {config['receiver']}")
        return True

    except Exception as e:
        logger.error(f"邮件推送失败: {e}")
        return False


async def send_enroll_result_email(course, success: bool, message: str = "") -> bool:
    """发送选课结果邮件"""
    config = _get_smtp_config()

    if not config["username"] or not config["password"] or not config["receiver"]:
        return False

    try:
        status = "成功 ✅" if success else "失败 ❌"

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"选课{status}: {course.name}"
        msg["From"] = config["username"]
        msg["To"] = config["receiver"]

        html = f"""
        <html><body style="font-family: sans-serif; padding:20px;">
        <h2>{'✅' if success else '❌'} 自动选课{status}</h2>
        <p><strong>课程：</strong>{course.name}</p>
        <p><strong>时间：</strong>{course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}</p>
        <p><strong>地点：</strong>{course.location}</p>
        {"<p><strong>备注：</strong>" + message + "</p>" if message else ""}
        </body></html>
        """
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(config["server"], config["port"], context=context) as server:
            server.login(config["username"], config["password"])
            server.send_message(msg)
        return True

    except Exception as e:
        logger.error(f"选课结果邮件发送失败: {e}")
        return False
