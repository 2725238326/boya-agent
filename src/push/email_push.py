"""
邮件推送模块
支持 Gmail SMTP 发送 HTML 格式课程通知邮件
支持多订阅者按个人偏好过滤推送
支持通过 HTTP CONNECT 代理发送（绕过云厂商端口封锁）
"""

import os
import ssl
import socket
import smtplib
import time
from datetime import datetime
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
        "from_default": os.getenv("SMTP_FROM", ""),
        "from_verify": os.getenv("SMTP_FROM_VERIFY", ""),
        "from_login": os.getenv("SMTP_FROM_LOGIN", ""),
        "from_notify": os.getenv("SMTP_FROM_NOTIFY", ""),
        "from_reminder": os.getenv("SMTP_FROM_REMINDER", ""),
    }


def _parse_bool(text: str, default: bool = True) -> bool:
    value = (text or "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _resolve_transport(config: dict, from_kind: str) -> dict:
    """
    解析发送通道：
    - verify/login 可走 SMTP_VERIFY_*
    - notify/reminder 可走 SMTP_NOTIFY_*
    - 其余回退默认 SMTP_*
    """
    transport_group = "default"
    if from_kind in {"verify", "login"}:
        transport_group = "verify"
    elif from_kind in {"notify", "reminder"}:
        transport_group = "notify"

    prefix_map = {
        "default": "SMTP",
        "verify": "SMTP_VERIFY",
        "notify": "SMTP_NOTIFY",
    }
    prefix = prefix_map[transport_group]

    server = os.getenv(f"{prefix}_SERVER", "").strip() or config["server"]
    port_text = os.getenv(f"{prefix}_PORT", "").strip()
    use_tls_text = os.getenv(f"{prefix}_USE_TLS", "").strip()
    username = os.getenv(f"{prefix}_USERNAME", "").strip() or config["username"]
    password = os.getenv(f"{prefix}_PASSWORD", "").strip() or config["password"]

    port = int(port_text) if port_text else int(config["port"])
    use_tls = _parse_bool(use_tls_text, default=bool(config["use_tls"])) if use_tls_text else bool(config["use_tls"])

    return {
        "group": transport_group,
        "server": server,
        "port": port,
        "use_tls": use_tls,
        "username": username,
        "password": password,
    }


def _pick_from_email(config: dict, from_kind: str, transport_group: str = "") -> str:
    """按邮件类型和实际 transport 选择发件人地址，未配置则回退到该通道账号"""
    key_map = {
        "verify": "from_verify",
        "login": "from_login",
        "notify": "from_notify",
        "reminder": "from_reminder",
    }
    transport_from_key = {
        "verify": "from_verify",
        "notify": "from_notify",
        "default": "from_default",
    }
    key = key_map.get(from_kind, "")
    candidate = ""
    if transport_group in transport_from_key:
        candidate = config.get(transport_from_key[transport_group], "")
    if not candidate and key:
        candidate = config.get(key, "")
    if not candidate:
        candidate = config.get("from_default", "")
    candidate = (candidate or "").strip()
    if candidate and "@" in candidate:
        return candidate
    transport_kind = transport_group if transport_group in {"verify", "notify"} else from_kind
    transport = _resolve_transport(config, transport_kind)
    return transport["username"] or config["username"]


def _get_proxy_config():
    """仅解析 SMTP_PROXY，避免误用系统级 HTTP(S) 代理影响邮件投递"""
    proxy_url = os.getenv("SMTP_PROXY", "").strip()
    if not proxy_url:
        return None
    try:
        from urllib.parse import urlparse
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port
        if host and port:
            return (host, port)
    except Exception as e:
        logger.warning(f"解析代理配置失败: {e}")
    return None


def _create_proxy_socket(dest_host: str, dest_port: int, timeout: int = 15):
    """通过 HTTP CONNECT 隧道创建到目标的 TCP 连接"""
    proxy = _get_proxy_config()
    if not proxy:
        return None

    proxy_host, proxy_port = proxy
    try:
        sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
        # 发送 CONNECT 请求
        connect_req = f"CONNECT {dest_host}:{dest_port} HTTP/1.1\r\nHost: {dest_host}:{dest_port}\r\n\r\n"
        sock.sendall(connect_req.encode())
        # 读取代理响应
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        response_line = response.decode("utf-8", errors="replace").split("\r\n")[0]
        if "200" in response_line:
            logger.debug(f"SMTP 代理隧道建立成功: {proxy_host}:{proxy_port} -> {dest_host}:{dest_port}")
            return sock
        else:
            sock.close()
            logger.error(f"代理 CONNECT 失败: {response_line}")
            return None
    except Exception as e:
        logger.error(f"代理连接失败: {e}")
        return None


def _send_with_transport(msg, transport: dict) -> bool:
    """使用指定的 transport 发送邮件"""
    if not transport["username"] or not transport["password"]:
        logger.error(f"未配置 SMTP 账号/密码: group={transport['group']}")
        return False

    proxy_sock = _create_proxy_socket(transport["server"], transport["port"])
    try:
        if transport["use_tls"]:
            if proxy_sock:
                server = smtplib.SMTP()
                server.timeout = 15
                server._host = transport["server"]
                server.sock = proxy_sock
                server.file = proxy_sock.makefile('rb')
                server.getreply()
                server.ehlo()
            else:
                server = smtplib.SMTP(transport["server"], transport["port"], timeout=15)
                server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(transport["username"], transport["password"])
            server.send_message(msg)
            server.quit()
        else:
            context = ssl.create_default_context()
            if proxy_sock:
                ssl_sock = context.wrap_socket(proxy_sock, server_hostname=transport["server"])
                server = smtplib.SMTP_SSL(context=context, timeout=15)
                server._host = transport["server"]
                server.sock = ssl_sock
                server.file = ssl_sock.makefile('rb')
                server.getreply()
                server.ehlo_or_helo_if_needed()
                server.login(transport["username"], transport["password"])
                server.send_message(msg)
                server.quit()
            else:
                with smtplib.SMTP_SSL(transport["server"], transport["port"], context=context, timeout=15) as server:
                    server.login(transport["username"], transport["password"])
                    server.send_message(msg)
        return True
    except Exception:
        if proxy_sock:
            try:
                proxy_sock.close()
            except Exception:
                pass
        raise


def _resolve_fallback_transport(config: dict, from_kind: str, primary: dict):
    """notify/reminder 主通道失败时，回退到 verify 通道"""
    if from_kind not in {"notify", "reminder"}:
        return None

    fallback = _resolve_transport(config, "verify")
    if not fallback["username"] or not fallback["password"]:
        return None

    if (
        fallback["group"] == primary["group"]
        and fallback["server"] == primary["server"]
        and fallback["port"] == primary["port"]
        and fallback["username"] == primary["username"]
    ):
        return None
    return fallback


def _send_raw_email(to_email: str, subject: str, html: str, from_kind: str = "notify") -> bool:
    """底层发邮件函数，notify/reminder 失败时自动回退到 verify 通道"""
    config = _get_smtp_config()
    primary = _resolve_transport(config, from_kind)
    if not primary["username"] or not primary["password"]:
        logger.error(f"未配置 SMTP 账号/密码: group={primary['group']}, kind={from_kind}")
        return False

    fallback = _resolve_fallback_transport(config, from_kind, primary)
    attempts = [(primary, "primary")]
    if from_kind in {"notify", "reminder"}:
        attempts.append((primary, "retry"))
    if fallback:
        attempts.append((fallback, f"fallback:{fallback['group']}"))

    last_error = None
    for transport, stage in attempts:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = _pick_from_email(config, from_kind, transport["group"])
            msg["To"] = to_email
            msg.attach(MIMEText(html, "html", "utf-8"))
            _send_with_transport(msg, transport)

            if stage == "retry":
                logger.warning(
                    f"邮件主通道重试成功 [{to_email}]: kind={from_kind}, group={transport['group']}"
                )
            elif stage.startswith("fallback:"):
                logger.warning(
                    f"邮件回退通道发送成功 [{to_email}]: kind={from_kind}, group={transport['group']}"
                )
            return True
        except Exception as e:
            last_error = e
            logger.error(
                f"邮件发送失败 [{to_email}]: kind={from_kind}, stage={stage}, "
                f"group={transport['group']}, server={transport['server']}:{transport['port']} - {e}"
            )
            if stage == "primary" and from_kind in {"notify", "reminder"}:
                time.sleep(0.8)

    if last_error:
        logger.error(f"邮件发送最终失败 [{to_email}]: {last_error}")
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
    ok = _send_raw_email(to_email, "验证你的博雅课程推送订阅", html, from_kind="verify")
    if ok:
        logger.info(f"验证邮件已发送: {to_email}")
    return ok


def send_login_email(to_email: str, login_url: str) -> bool:
    """发送登录链接邮件（免密码）"""
    body = f"""
<p style="font-size:15px; color:{_EMAIL_TEXT}; line-height:1.6; text-align:center; margin:0 0 24px;">
  点击下方按钮登录你的博雅课程门户
</p>
<table role="presentation" width="100%"><tr><td align="center">
  <a href="{login_url}" style="display:inline-block; padding:14px 40px; background:{_EMAIL_ACCENT};
     color:#fff; text-decoration:none; border-radius:12px; font-weight:600; font-size:16px;">
    登录门户
  </a>
</td></tr></table>
<p style="font-size:13px; color:{_EMAIL_TEXT}; text-align:center; margin:16px 0 0; line-height:1.6;">
  若在 QQ 邮箱内无法打开，请点击右上角「在浏览器打开」后再登录
</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin-top:14px; background:#f5f5f7; border:1px solid #e5e5ea; border-radius:10px;">
<tr><td style="padding:12px;">
  <p style="margin:0 0 6px; font-size:12px; color:{_EMAIL_MUTED};">复制链接到系统浏览器打开：</p>
  <p style="margin:0; font-size:13px; color:{_EMAIL_TEXT}; word-break:break-all;">{login_url}</p>
</td></tr></table>
<p style="font-size:12px; color:{_EMAIL_MUTED}; text-align:center; margin:20px 0 0; word-break:break-all;">
  如按钮无法点击，请复制链接：<br>{login_url}
</p>"""
    html = _email_shell("登录你的门户", body)
    ok = _send_raw_email(to_email, "登录你的博雅课程门户", html, from_kind="login")
    if ok:
        logger.info(f"登录邮件已发送: {to_email}")
        return True

    # 短暂重试 1 次，提升偶发网络波动下的成功率
    time.sleep(0.8)
    retry_ok = _send_raw_email(to_email, "登录你的博雅课程门户", html, from_kind="login")
    if retry_ok:
        logger.info(f"登录邮件重试成功: {to_email}")
    return retry_ok


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


def _describe_subscription_reason(sub) -> str:
    """Build a human-readable summary of subscriber preferences."""
    campus_text = sub.campus_filter or "\u5168\u90e8\u6821\u533a"
    categories = sub.categories or []
    category_text = "\u3001".join(categories) if categories else "\u5168\u90e8\u7c7b\u522b"
    sign_text = "\u4ec5\u81ea\u4e3b\u7b7e\u5230\u8bfe\u7a0b" if sub.self_sign_only else "\u5305\u542b\u5e38\u89c4\u7b7e\u5230\u8bfe\u7a0b"
    return f"{campus_text} / {category_text} / {sign_text}"


def _build_notification_subject(event_type: str, delivery_mode: str, course_count: int) -> str:
    """Build notification email subject."""
    if event_type == "snipe":
        return f"\u535a\u96c5\u8bfe\u7a0b\u9000\u8bfe\u8865\u5f55\u63d0\u9192 ({course_count} \u95e8)"
    if delivery_mode == "priority":
        return f"\u535a\u96c5\u8bfe\u7a0b\u5373\u65f6\u63d0\u9192 ({course_count} \u95e8)"
    if delivery_mode == "digest_urgent":
        return f"\u535a\u96c5\u8bfe\u7a0b\u8fd1\u671f\u6458\u8981 ({course_count} \u95e8)"
    if delivery_mode == "digest_soon":
        return f"\u535a\u96c5\u8bfe\u7a0b\u65b0\u8bfe\u6458\u8981 ({course_count} \u95e8)"
    if delivery_mode == "digest_daily":
        return f"\u535a\u96c5\u8bfe\u7a0b\u4eca\u65e5\u6c47\u603b ({course_count} \u95e8)"
    return f"\u535a\u96c5\u65b0\u8bfe\u7a0b\u901a\u77e5 ({course_count} \u95e8)"


def _build_notification_intro(event_type: str, delivery_mode: str, course_count: int) -> tuple[str, str]:
    """Return email heading and intro copy."""
    if event_type == "snipe":
        return (
            "\u4f60\u5173\u6ce8\u7684\u8bfe\u7a0b\u51fa\u73b0\u7a7a\u51fa\u540d\u989d",
            "\u8fd9\u7c7b\u901a\u77e5\u4f1a\u4f18\u5148\u53d1\u9001\uff0c\u5e2e\u4f60\u66f4\u5feb\u53d1\u73b0\u53ef\u4ee5\u7acb\u5373\u5c1d\u8bd5\u7684\u9000\u8bfe\u8865\u5f55\u8bfe\u7a0b\u3002",
        )
    if delivery_mode == "priority":
        return (
            "\u9002\u5408\u7acb\u5373\u5904\u7406\u7684\u8bfe\u7a0b\u5df2\u51fa\u73b0",
            "\u8fd9\u4e9b\u8bfe\u7a0b\u8981\u4e48\u5df2\u7ecf\u5f00\u62a2\uff0c\u8981\u4e48\u5373\u5c06\u5f00\u59cb\u9009\u8bfe\uff0c\u6240\u4ee5\u6ca1\u6709\u8d70\u6458\u8981\uff0c\u76f4\u63a5\u5355\u72ec\u63d0\u9192\u3002",
        )
    if delivery_mode == "digest_urgent":
        return (
            "\u8fc7\u53bb\u51e0\u5206\u949f\u7684\u8fd1\u671f\u8bfe\u7a0b\u6458\u8981",
            "\u4e3a\u4e86\u51cf\u5c11\u90ae\u4ef6\u6253\u6270\uff0c\u7cfb\u7edf\u4f1a\u628a\u76f8\u8fd1\u65f6\u95f4\u5185\u51fa\u73b0\u7684\u65b0\u8bfe\u7a0b\u5408\u5e76\u6210\u4e00\u5c01\u90ae\u4ef6\u3002\u8fd9\u662f\u6700\u8fd1\u4e00\u6279\u7684\u8bfe\u7a0b\u6458\u8981\u3002",
        )
    if delivery_mode == "digest_soon":
        return (
            "\u65b0\u53d1\u73b0\u8bfe\u7a0b\u6458\u8981",
            "\u8fd9\u5c01\u90ae\u4ef6\u628a\u6700\u8fd1\u4e00\u6279\u65b0\u8bfe\u7a0b\u5408\u5e76\u5728\u4e00\u8d77\uff0c\u65b9\u4fbf\u4f60\u4e00\u6b21\u6027\u8bfb\u5b8c\u518d\u51b3\u5b9a\u662f\u5426\u5173\u6ce8\u3002",
        )
    if delivery_mode == "digest_daily":
        return (
            "\u4eca\u65e5\u503c\u5f97\u5173\u6ce8\u7684\u8bfe\u7a0b\u6c47\u603b",
            "\u8fd9\u662f\u4eca\u5929\u5c1a\u672a\u5355\u72ec\u63a8\u9001\u8fc7\u7684\u8bfe\u7a0b\u6c47\u603b\uff0c\u65b9\u4fbf\u4f60\u5728\u4e00\u5c01\u90ae\u4ef6\u4e2d\u96c6\u4e2d\u67e5\u770b\u3002",
        )
    return (
        f"\u53d1\u73b0 {course_count} \u95e8\u7b26\u5408\u4f60\u504f\u597d\u7684\u65b0\u8bfe\u7a0b",
        "\u8fd9\u4e9b\u8bfe\u7a0b\u5df2\u7ecf\u6839\u636e\u4f60\u7684\u8ba2\u9605\u504f\u597d\u8fdb\u884c\u8fc7\u6ee4\uff0c\u53ea\u4fdd\u7559\u66f4\u4e0e\u4f60\u76f8\u5173\u7684\u5185\u5bb9\u3002",
    )


def _build_notification_html(
    courses: list,
    unsubscribe_url: str = "",
    sub_token: str = "",
    base_url: str = "",
    event_type: str = "new",
    delivery_mode: str = "instant",
    subscriber=None,
) -> str:
    """Build full notification email HTML."""
    cards = []
    for c in courses:
        remind_url = f"{base_url}/api/remind/{sub_token}/{c.id}" if sub_token and base_url else ""
        cards.append(_build_course_html(c, remind_url))

    cards_html = "\n".join(cards)
    heading, intro = _build_notification_intro(event_type, delivery_mode, len(courses))

    unsub_link = ""
    if unsubscribe_url:
        unsub_link = f' | <a href="{unsubscribe_url}" style="color:{_EMAIL_ACCENT};">\u9000\u8ba2</a>'

    reason_html = ""
    if subscriber is not None:
        reason_html = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin:0 0 16px; background:#f5f5f7; border:1px solid #e5e5ea; border-radius:14px;">
<tr><td style="padding:14px 16px;">
  <p style="margin:0 0 6px; font-size:12px; color:{_EMAIL_MUTED};">\u4f60\u6536\u5230\u8fd9\u5c01\u90ae\u4ef6\uff0c\u56e0\u4e3a\u4f60\u7684\u8ba2\u9605\u504f\u597d\u662f\uff1a</p>
  <p style="margin:0; font-size:14px; color:{_EMAIL_TEXT}; line-height:1.6;">{_describe_subscription_reason(subscriber)}</p>
</td></tr></table>"""

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
  <h1 style="margin:0; color:#fff; font-size:22px; font-weight:700;">{heading}</h1>
  <p style="margin:6px 0 0; color:rgba(255,255,255,0.75); font-size:14px;">\u5171\u6709 {len(courses)} \u95e8\u8bfe\u7a0b\u503c\u5f97\u5173\u6ce8</p>
</td></tr>
<tr><td style="padding:24px;">
<p style="margin:0 0 14px; font-size:15px; line-height:1.7; color:{_EMAIL_TEXT};">{intro}</p>
{reason_html}
{cards_html}
</td></tr>
<tr><td style="padding:16px 24px; border-top:1px solid #f0f0f0; text-align:center; font-size:12px; color:{_EMAIL_MUTED};">
  BUAA \u535a\u96c5\u8bfe\u7a0b\u63a8\u9001{unsub_link}
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


async def send_email_to_subscribers(
    courses: list,
    base_url: str = "",
    event_type: str = "new",
    delivery_mode: str = "instant",
) -> int:
    """Send course notifications to all active verified subscribers."""
    from src.models import EmailSubscriber, NotificationEvent, get_session

    session = get_session()
    try:
        subs = (
            session.query(EmailSubscriber)
            .filter_by(verified=True, active=True)
            .all()
        )
        if not subs:
            logger.info("\u6ca1\u6709\u6d3b\u8dc3\u7684\u90ae\u4ef6\u8ba2\u9605\u8005")
            return 0

        sent_count = 0
        now = datetime.now()
        for sub in subs:
            # 检查用户是否已暂停推送
            paused_until = getattr(sub, "push_paused_until", None)
            if paused_until and now < paused_until:
                logger.info(f"推送已暂停，跳过: {sub.email} (暂停至 {paused_until.strftime('%Y-%m-%d %H:%M')})")
                continue

            filtered = _filter_for_subscriber(courses, sub)
            if not filtered:
                continue

            unsub_url = f"{base_url}/api/unsubscribe/{sub.token}" if base_url else ""
            html = _build_notification_html(
                filtered,
                unsub_url,
                sub_token=sub.token,
                base_url=base_url,
                event_type=event_type,
                delivery_mode=delivery_mode,
                subscriber=sub,
            )
            subject = _build_notification_subject(event_type, delivery_mode, len(filtered))
            ok = _send_raw_email(sub.email, subject, html, from_kind="notify")
            if ok:
                sent_count += 1
                logger.info(f"\u90ae\u4ef6\u63a8\u9001\u6210\u529f: {len(filtered)} \u95e8\u8bfe\u7a0b -> {sub.email}")
            else:
                logger.warning(f"\u90ae\u4ef6\u63a8\u9001\u5931\u8d25: {sub.email}")

            for course in filtered:
                event = NotificationEvent(
                    subscriber_id=sub.id,
                    subscriber_email=sub.email,
                    course_id=course.id,
                    course_name=course.name,
                    course_category=getattr(course, "category", "") or "",
                    event_type=event_type,
                    delivery_mode=delivery_mode,
                    channel="email",
                    success=ok,
                    message=f"matched={len(filtered)}",
                )
                session.add(event)

        session.commit()
        return sent_count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def send_email_notification(
    courses: list,
    event_type: str = "new",
    delivery_mode: str = "instant",
) -> bool:
    """Compatibility wrapper for subscriber email pushes."""
    count = await send_email_to_subscribers(courses, event_type=event_type, delivery_mode=delivery_mode)
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
            if _send_raw_email(sub.email, f"选课{status_label}: {course.name}", html, from_kind="notify"):
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
    return _send_raw_email(to_email, f"选课提醒：{course.name}", html, from_kind="reminder")
