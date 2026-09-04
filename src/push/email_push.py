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
import re
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from html import escape as html_escape
from typing import List
from urllib.parse import quote
from loguru import logger

from src.course_state import get_check_in_display_label, is_course_expired, is_self_check_in
from src.notification_jobs import (
    NotificationDeliveryResult,
    drain_notification_jobs,
    enqueue_notification_job,
)
from src.time_utils import now as business_now


def _mask_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    if "@" not in normalized:
        return "匿名"
    local, domain = normalized.split("@", 1)
    if len(local) <= 2:
        return f"{local[:1]}*@{domain}"
    return f"{local[:2]}{'*' * max(1, len(local) - 2)}@{domain}"


def _html(value) -> str:
    """Escape text before placing it in the hand-written HTML email templates."""
    return html_escape(str(value if value is not None else ""), quote=True)


def _email_subject_text(value) -> str:
    """Keep dynamic mail subject text single-line and header-safe."""
    return re.sub(r"[\r\n]+", " ", str(value if value is not None else "")).strip()


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
    if not isinstance(to_email, str) or any(ord(char) < 32 or ord(char) == 127 for char in to_email):
        logger.error("邮件收件人地址包含非法控制字符，已拒绝发送")
        return False
    masked_recipient = _mask_email(to_email)
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
            msg["Subject"] = _email_subject_text(subject)
            sender = _pick_from_email(config, from_kind, transport["group"])
            if any(ord(char) < 32 or ord(char) == 127 for char in sender):
                logger.error("邮件发件人地址包含非法控制字符，已拒绝发送")
                return False
            msg["From"] = formataddr(("\u8a00\u828a\u828a", sender))
            msg["To"] = to_email
            msg.attach(MIMEText(html, "html", "utf-8"))
            _send_with_transport(msg, transport)

            if stage == "retry":
                logger.warning(
                    f"邮件主通道重试成功 [{masked_recipient}]: kind={from_kind}, group={transport['group']}"
                )
            elif stage.startswith("fallback:"):
                logger.warning(
                    f"邮件回退通道发送成功 [{masked_recipient}]: kind={from_kind}, group={transport['group']}"
                )
            return True
        except Exception as e:
            last_error = e
            logger.error(
                f"邮件发送失败 [{masked_recipient}]: kind={from_kind}, stage={stage}, "
                f"group={transport['group']}, server={transport['server']}:{transport['port']} - {e}"
            )
            if stage == "primary" and from_kind in {"notify", "reminder"}:
                time.sleep(0.8)

    if last_error:
        logger.error(f"邮件发送最终失败 [{_mask_email(to_email)}]: {last_error}")
    return False


# ========== 通用样式 ==========

_EMAIL_ACCENT = "#0071e3"  # Apple blue
_EMAIL_BG = "#f2f2f5"
_EMAIL_CARD_BG = "#ffffff"
_EMAIL_TEXT = "#1d1d1f"
_EMAIL_MUTED = "#86868b"
_EMAIL_HAIRLINE = "#e8e8ed"


def _email_primary_button(url: str, label: str) -> str:
    return f"""
<table role="presentation" width="100%" style="margin:0 0 18px;"><tr><td align="center">
  <a href="{_html(url)}" class="email-primary-btn" style="display:inline-block; min-width:220px; padding:14px 30px; background:{_EMAIL_TEXT};
     color:#fff; text-decoration:none; border-radius:999px; font-weight:700; font-size:15px;
     box-shadow:0 8px 20px rgba(29,29,31,0.14);">
    {_html(label)}
  </a>
</td></tr></table>"""


def _email_info_panel(eyebrow: str, title: str, body_html: str) -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="email-info-panel"
       style="margin:0 0 18px; background:#fbfbfd; border:1px solid {_EMAIL_HAIRLINE}; border-radius:24px;">
<tr><td style="padding:16px 18px;">
  <p style="margin:0 0 8px; font-size:12px; color:{_EMAIL_MUTED}; letter-spacing:0.08em;">{_html(eyebrow)}</p>
  <p style="margin:0 0 8px; font-size:20px; color:{_EMAIL_TEXT}; font-weight:700; line-height:1.45;">{_html(title)}</p>
  <div style="font-size:14px; color:{_EMAIL_TEXT}; line-height:1.8;">{body_html}</div>
</td></tr></table>"""


def _email_link_fallback(url: str, label: str = "如按钮无法点击，请复制链接：") -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="email-link-fallback"
       style="margin:14px 0 0; background:#fbfbfd; border:1px solid {_EMAIL_HAIRLINE}; border-radius:18px;">
<tr><td style="padding:14px 16px;">
  <p style="margin:0 0 6px; font-size:12px; color:{_EMAIL_MUTED};">{_html(label)}</p>
  <p style="margin:0; font-size:13px; color:{_EMAIL_TEXT}; word-break:break-all;">{_html(url)}</p>
</td></tr></table>"""


def _email_shell(title: str, body_html: str, footer_html: str = "", eyebrow: str = "博雅课程") -> str:
    """统一的邮件外壳模板 — 移动端 & 桌面端双适配"""
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{_html(title)}</title>
<style>
  :root {{
    color-scheme: light dark;
    supported-color-schemes: light dark;
  }}
  @media (prefers-color-scheme: dark) {{
    body, .email-page {{
      background: #111214 !important;
      color: #f5f5f7 !important;
    }}
    .email-card {{
      background: #1c1d20 !important;
      box-shadow: 0 18px 48px rgba(0, 0, 0, 0.34) !important;
    }}
    .email-header {{
      background: linear-gradient(180deg, #24262b 0%, #1c1d20 100%) !important;
      border-bottom-color: #34363d !important;
    }}
    .email-footer {{
      border-top-color: #34363d !important;
      color: #a1a1aa !important;
    }}
    .email-info-panel,
    .email-link-fallback {{
      background: #24262b !important;
      border-color: #34363d !important;
    }}
    .email-primary-btn {{
      background: #f5f5f7 !important;
      color: #111214 !important;
      box-shadow: none !important;
    }}
    a {{
      color: #7ab8ff !important;
    }}
  }}
</style>
</head>
<body style="margin:0; padding:0; background:{_EMAIL_BG};
             font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Roboto,Helvetica,Arial,sans-serif;
             -webkit-font-smoothing:antialiased; color:{_EMAIL_TEXT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="email-page" style="background:{_EMAIL_BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="email-card" style="max-width:560px; background:{_EMAIL_CARD_BG};
       border-radius:28px; overflow:hidden; box-shadow:0 18px 48px rgba(15,23,42,0.07);">
<!-- Header -->
<tr><td class="email-header" style="padding:34px 28px 22px; text-align:left; background:linear-gradient(180deg, #fafcff 0%, #ffffff 100%); border-bottom:1px solid {_EMAIL_HAIRLINE};">
  <p style="margin:0 0 10px; color:{_EMAIL_MUTED}; font-size:12px; font-weight:700; letter-spacing:0.12em;">{_html(eyebrow)}</p>
  <h1 style="margin:0; color:{_EMAIL_TEXT}; font-size:28px; font-weight:700; line-height:1.28; letter-spacing:-0.02em;">{_html(title)}</h1>
</td></tr>
<!-- Body -->
<tr><td style="padding:28px 28px 26px;">
{body_html}
</td></tr>
<!-- Footer -->
<tr><td class="email-footer" style="padding:16px 24px; border-top:1px solid #f0f0f0; text-align:center; font-size:12px; color:{_EMAIL_MUTED};">
  BUAA 博雅课程推送{footer_html}
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


# ========== 验证和登录邮件 ==========


def send_login_code_email(to_email: str, login_url: str, login_code: str, subscribe_url: str) -> bool:
    """发送一次性登录链接和验证码；链接只能使用一次且会自动过期。"""
    code_digits = len(login_code or "")
    verification_panel_body = (
        f'本次验证码：<strong style="font-size:24px; letter-spacing:0.18em;">'
        f'{login_code}</strong><br>验证码会在一段时间后失效，且成功使用后不能再次使用。'
    )
    body = f"""
<p style="margin:0 0 14px; font-size:15px; line-height:1.7; color:{_EMAIL_TEXT};">
  这是一封登录博雅课程门户的确认邮件。如果这是你发起的请求，请使用下方的一次性链接，
  或回到订阅页输入 {code_digits} 位验证码。
</p>
{_email_primary_button(login_url, "一次性进入课程门户")}
{_email_info_panel("登录验证码", "输入验证码也可以登录", verification_panel_body)}
{_email_link_fallback(subscribe_url, "如果按钮无法打开，请复制下面的订阅页地址并输入验证码：")}
{_email_link_fallback(login_url, "备用：一次性登录链接：")}"""
    html = _email_shell("登录你的博雅课程门户", body, eyebrow="邮箱登录")
    ok = _send_raw_email(to_email, "登录你的博雅课程门户", html, from_kind="login")
    if ok:
        logger.info(f"一次性登录邮件已发送: {_mask_email(to_email)}")
        return True
    time.sleep(0.8)
    retry_ok = _send_raw_email(to_email, "登录你的博雅课程门户", html, from_kind="login")
    if retry_ok:
        logger.info(f"一次性登录邮件重试成功: {_mask_email(to_email)}")
    return retry_ok


def send_verification_email(to_email: str, verify_url: str, verify_code: str, subscribe_url: str) -> bool:
    """发送邮箱验证码邮件。"""
    code_digits = len(verify_code or "")
    safe_subscribe_url = _html(subscribe_url)
    guide_panel = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin:0 0 18px; background:#f8fafc; border:1px solid {_EMAIL_HAIRLINE}; border-radius:24px;">
<tr><td style="padding:18px 18px 14px;">
  <p style="margin:0 0 10px; font-size:13px; color:{_EMAIL_TEXT}; font-weight:700;">推荐使用方式</p>
  <p style="margin:0; font-size:13px; color:{_EMAIL_MUTED}; line-height:1.75;">
    直接回到
    <a href="{safe_subscribe_url}" style="color:{_EMAIL_ACCENT}; text-decoration:none; font-weight:700;">buaaboya.top/subscribe</a>
    ，输入下方的 {code_digits} 位验证码完成验证。<br>
    验证完成后即可进入课程门户，后续再设置校区、类别和签到偏好即可。
  </p>
</td></tr></table>"""

    code_panel = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin:0 0 18px; background:#f8fafc; border:1px solid {_EMAIL_HAIRLINE}; border-radius:24px;">
<tr><td style="padding:18px 18px 16px; text-align:center;">
  <p style="margin:0 0 8px; font-size:12px; color:{_EMAIL_MUTED}; letter-spacing:0.08em;">本次验证码</p>
  <p style="margin:0 0 8px; font-size:30px; letter-spacing:0.24em; font-weight:800; color:{_EMAIL_TEXT};">{verify_code}</p>
  <p style="margin:0; font-size:13px; color:{_EMAIL_MUTED}; line-height:1.7;">
    如果邮件里的按钮打不开，也没关系，回到订阅页输入这个验证码即可完成验证。
  </p>
</td></tr></table>"""

    verification_panel_body = (
        f"建议优先回到订阅页，输入这封邮件里的 {code_digits} 位验证码完成验证。"
        "邮件里的链接保留为备用方式。"
    )

    body = f"""
<p style="margin:0 0 14px; font-size:15px; line-height:1.7; color:{_EMAIL_TEXT};">
  欢迎使用 BUAA 博雅课程提醒。为了确认这个邮箱可用，请先完成一次邮箱验证。
</p>
{_email_info_panel("验证邮箱", "推荐直接输入验证码", verification_panel_body)}
{guide_panel}
{_email_primary_button(subscribe_url, "打开订阅页输入验证码")}
{code_panel}
{_email_link_fallback(subscribe_url, "如果按钮无法打开，请复制下面的订阅页地址到系统浏览器：")}
{_email_link_fallback(verify_url, "备用方式：如果你更习惯直接使用验证链接，也可以复制下面这条链接到系统浏览器：")}"""

    html = _email_shell("验证你的邮箱", body, eyebrow="邮箱验证")
    ok = _send_raw_email(to_email, "验证你的博雅课程提醒订阅", html, from_kind="verify")
    if ok:
        logger.info(f"验证码邮件已发送: {_mask_email(to_email)}")
    return ok


def send_service_update_email(to_email: str, home_url: str, portal_url: str, subscribe_url: str) -> bool:
    """发送站点入口调整通知邮件。"""
    safe_home_url = _html(home_url)
    safe_portal_url = _html(portal_url)
    safe_subscribe_url = _html(subscribe_url)
    entry_panel_body = f"""
  <p style="margin:0 0 8px;">你现在可以直接访问
    <a href="{safe_home_url}" style="color:{_EMAIL_ACCENT}; text-decoration:none; font-weight:700;">buaaboya.top</a>。
  </p>
  <p style="margin:0 0 8px;">原有订阅设置、提醒偏好和账户数据都已保留，不需要重新注册。</p>
  <p style="margin:0;">如果你之前已经在当前浏览器登录过，通常可以继续保持登录状态；如果没有保留，从首页进入后重新获取一次登录邮件即可。</p>
"""

    quick_links_body = f"""
  <p style="margin:0 0 8px;">首页：<a href="{safe_home_url}" style="color:{_EMAIL_ACCENT}; text-decoration:none; font-weight:700;">{safe_home_url}</a></p>
  <p style="margin:0 0 8px;">个人门户：<a href="{safe_portal_url}" style="color:{_EMAIL_ACCENT}; text-decoration:none; font-weight:700;">{safe_portal_url}</a></p>
  <p style="margin:0;">邮箱订阅页：<a href="{safe_subscribe_url}" style="color:{_EMAIL_ACCENT}; text-decoration:none; font-weight:700;">{safe_subscribe_url}</a></p>
"""

    body = f"""
<p style="margin:0 0 14px; font-size:15px; line-height:1.7; color:{_EMAIL_TEXT};">
  我们对博雅课程提醒站点做了一次入口整理。现在直接访问 <strong>buaaboya.top</strong> 就可以进入首页，不需要再记额外入口。
</p>
{_email_info_panel("入口调整", "现在从首页进入更方便", entry_panel_body)}
{_email_primary_button(home_url, "打开博雅课程提醒首页")}
{_email_info_panel("常用入口", "你可以这样继续使用", quick_links_body)}
{_email_link_fallback(home_url, "如果按钮无法打开，请复制下面的首页地址到系统浏览器：")}"""

    html = _email_shell("站点入口调整通知", body, eyebrow="服务调整")
    ok = _send_raw_email(to_email, "站点入口更新：现在直接访问 buaaboya.top 即可", html, from_kind="notify")
    if ok:
        logger.info(f"站点调整通知邮件已发送: {_mask_email(to_email)}")
    return ok


def send_service_update_to_subscribers(subscribers: List, base_url: str) -> dict:
    """向现有已验证用户发送站点入口调整通知。"""
    home_url = base_url.rstrip("/") or "https://buaaboya.top"
    portal_url = f"{home_url}/portal"
    subscribe_url = f"{home_url}/subscribe"

    success = 0
    failed = 0
    total = 0

    for subscriber in subscribers:
        email = (getattr(subscriber, "email", "") or "").strip().lower()
        if not email:
            continue
        total += 1
        ok = send_service_update_email(email, home_url, portal_url, subscribe_url)
        if ok:
            success += 1
        else:
            failed += 1
        time.sleep(0.15)

    return {
        "total": total,
        "success": success,
        "failed": failed,
    }


def _build_course_html(course, remind_url: str = "", portal_url: str = "") -> str:
    """构建单条课程 HTML 卡片 — 移动端友好的单列布局"""
    course_name = _html(getattr(course, "name", ""))
    category = _html(getattr(course, "category", ""))
    teacher = _html(getattr(course, "teacher", ""))
    campus = _html(getattr(course, "campus", ""))
    location = _html(getattr(course, "location", ""))
    check_in = _html(get_check_in_display_label(course))
    is_self_sign = is_self_check_in(course)
    sign_color = "#34c759" if is_self_sign else "#ff9500"
    remaining = course.remaining
    cap_color = "#34c759" if remaining > 10 else ("#ff9500" if remaining > 0 else "#ff3b30")
    urgency_text = "建议立即查看" if (course.enroll_start and course.enroll_start <= business_now()) or remaining <= 10 else "可以加入关注"
    urgency_bg = "#fff7ed" if urgency_text == "建议立即查看" else "#f3f4f6"
    urgency_color = "#c2410c" if urgency_text == "建议立即查看" else "#6b7280"

    enroll_start_str = course.enroll_start.strftime('%m/%d %H:%M') if course.enroll_start else '未知'
    start_str = course.start_time.strftime('%m/%d %H:%M') if course.start_time else '未知'

    action_buttons = []
    if portal_url:
        action_buttons.append(
            f"""<a href="{_html(portal_url)}" style="display:inline-block; padding:10px 18px; background:{_EMAIL_TEXT};
     color:#fff; text-decoration:none; border-radius:999px; font-weight:700; font-size:13px;">打开门户</a>"""
        )
    if remind_url:
        action_buttons.append(
            f"""<a href="{_html(remind_url)}" style="display:inline-block; padding:10px 18px; background:#ffffff;
     color:{_EMAIL_TEXT}; text-decoration:none; border-radius:999px; font-weight:600; font-size:13px;
     border:1px solid #d1d5db;">提醒我选课</a>"""
        )
    actions_html = ""
    if action_buttons:
        joined_buttons = "&nbsp;".join(action_buttons)
        actions_html = f"""
<table role="presentation" width="100%" style="margin-top:14px;"><tr><td align="center">
  {joined_buttons}
</td></tr></table>"""

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="margin:0 0 16px; border:1px solid #e5e7eb; border-radius:20px; overflow:hidden; background:#fbfbfd;">
<tr><td style="padding:20px;">
  <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 12px;">
    <tr><td style="padding:6px 10px; border-radius:999px; background:{urgency_bg}; color:{urgency_color}; font-size:12px; font-weight:600;">
      {urgency_text}
    </td></tr>
  </table>
  <p style="margin:0 0 4px; font-size:17px; font-weight:600; color:{_EMAIL_TEXT}; line-height:1.4;">
    {course_name}
  </p>
  <p style="margin:0 0 14px; font-size:13px; color:{_EMAIL_MUTED};">
    {category} · {teacher} · {campus}
  </p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:14px; color:{_EMAIL_TEXT};">
    <tr>
      <td style="padding:8px 0; width:50%;"><span style="color:{_EMAIL_MUTED}">地点</span><br><span style="font-weight:600;">{location}</span></td>
      <td style="padding:8px 0;"><span style="color:{_EMAIL_MUTED}">签到方式</span><br><span style="color:{sign_color}; font-weight:700;">{check_in}</span></td>
    </tr>
    <tr>
      <td style="padding:8px 0;"><span style="color:{_EMAIL_MUTED}">上课时间</span><br><span style="font-weight:600;">{start_str}</span></td>
      <td style="padding:8px 0;"><span style="color:{_EMAIL_MUTED}">剩余名额</span><br><span style="color:{cap_color}; font-weight:700;">剩余 {remaining} 人</span></td>
    </tr>
    <tr>
      <td colspan="2" style="padding:8px 0;"><span style="color:{_EMAIL_MUTED}">选课开始</span><br><span style="font-weight:700; color:{_EMAIL_ACCENT};">{enroll_start_str}</span></td>
    </tr>
  </table>
  {actions_html}
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
    if delivery_mode == "active_watch":
        return f"\u535a\u96c5\u8bfe\u7a0b\u5f00\u9009\u76d1\u63a7\u63d0\u9192 ({course_count} \u95e8)"
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
    if delivery_mode == "active_watch":
        return (
            "\u5df2\u5f00\u9009\u8bfe\u7a0b\u51fa\u73b0\u4e86\u503c\u5f97\u518d\u770b\u4e00\u773c\u7684\u53d8\u5316",
            "\u8fd9\u4e9b\u8bfe\u7a0b\u5df2\u7ecf\u5728\u9009\u8bfe\u7a97\u53e3\u5185\uff0c\u7cfb\u7edf\u53ea\u5728\u521a\u5f00\u9009\u6216\u540d\u989d\u8f83\u5145\u88d5\u65f6\u518d\u63d0\u9192\uff0c\u907f\u514d\u5bf9\u540c\u4e00\u95e8\u8bfe\u8fc7\u4e8e\u9891\u7e41\u5730\u91cd\u590d\u53d1\u9001\u3002",
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
    pause_url: str = "",
    sub_token: str = "",
    base_url: str = "",
    event_type: str = "new",
    delivery_mode: str = "instant",
    subscriber=None,
) -> str:
    """Build full notification email HTML."""
    cards = []
    # 门户只接受当前浏览器的 HttpOnly 会话；邮件中的长期订阅 token 不能作为登录凭据。
    portal_url = f"{base_url}/portal" if base_url else ""
    for c in courses:
        remind_url = (
            f"{base_url}/api/remind/{quote(str(sub_token), safe='')}/{quote(str(c.id), safe='')}"
            if sub_token and base_url
            else ""
        )
        cards.append(_build_course_html(c, remind_url, portal_url))

    cards_html = "\n".join(cards)
    heading, intro = _build_notification_intro(event_type, delivery_mode, len(courses))
    first_course = courses[0] if courses else None
    next_action_html = ""
    if first_course is not None:
        enroll_text = first_course.enroll_start.strftime('%m/%d %H:%M') if getattr(first_course, "enroll_start", None) else "时间待定"
        capacity_text = f"剩余 {first_course.remaining} 人"
        first_course_name = _html(getattr(first_course, "name", ""))
        first_course_campus = _html(getattr(first_course, "campus", "全部校区"))
        next_action_html = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notify-info-panel"
       style="margin:0 0 20px; background:#fbfbfd; border:1px solid {_EMAIL_HAIRLINE}; border-radius:24px;">
<tr><td style="padding:16px 18px;">
  <p style="margin:0 0 8px; font-size:12px; color:{_EMAIL_MUTED}; letter-spacing:0.08em;">优先查看</p>
  <p style="margin:0 0 8px; font-size:20px; color:{_EMAIL_TEXT}; font-weight:700; line-height:1.45;">{first_course_name}</p>
  <p style="margin:0; font-size:14px; color:{_EMAIL_TEXT}; line-height:1.8;">
    选课开始：<span style="font-weight:700; color:{_EMAIL_ACCENT};">{enroll_text}</span><br>
    当前状态：<span style="font-weight:700;">{capacity_text}</span> · {first_course_campus}
  </p>
</td></tr></table>"""

    unsub_link = ""
    if unsubscribe_url:
        unsub_link = f' | <a href="{_html(unsubscribe_url)}" style="color:{_EMAIL_ACCENT};">\u9000\u8ba2</a>'

    portal_button_html = ""
    if portal_url:
        portal_button_html = f"""
<table role="presentation" width="100%" style="margin:0 0 18px;"><tr><td align="center">
  <a href="{_html(portal_url)}" class="notify-primary-btn" style="display:inline-block; min-width:220px; padding:13px 28px; background:{_EMAIL_TEXT};
     color:#fff; text-decoration:none; border-radius:999px; font-weight:700; font-size:15px;">
    打开门户查看全部课程
  </a>
</td></tr></table>"""

    reason_html = ""
    if subscriber is not None:
        reason_html = f"""
<p style="margin:0 0 16px; font-size:13px; color:{_EMAIL_MUTED}; line-height:1.7;">
  发送依据：{_html(_describe_subscription_reason(subscriber))}
</p>"""

    manage_notice_html = ""
    if unsubscribe_url or portal_url or pause_url:
        actions = []
        if portal_url:
            actions.append(
                f"""<a href="{_html(portal_url)}" style="display:inline-block; padding:9px 14px; border-radius:999px;
background:rgba(0,113,227,0.08); color:{_EMAIL_ACCENT}; text-decoration:none; font-size:13px; font-weight:700;">
去门户调整提醒
</a>"""
            )
        if pause_url:
            actions.append(
                f"""<a href="{_html(pause_url)}" style="display:inline-block; padding:9px 14px; border-radius:999px;
background:rgba(255,149,0,0.10); color:#b45309; text-decoration:none; font-size:13px; font-weight:700;">
暂停 24 小时
</a>"""
            )
        if unsubscribe_url:
            actions.append(
                f"""<a href="{_html(unsubscribe_url)}" style="display:inline-block; padding:9px 14px; border-radius:999px;
background:rgba(255,59,48,0.08); color:#d93025; text-decoration:none; font-size:13px; font-weight:700;">
一键退订邮件
</a>"""
            )
        actions_html = "&nbsp;".join(actions)
        manage_notice_html = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notify-manage-panel"
       style="margin:0 0 18px; background:#fff8f2; border:1px solid #f5dfcf; border-radius:20px;">
<tr><td style="padding:14px 16px;">
  <p style="margin:0 0 6px; font-size:13px; color:{_EMAIL_TEXT}; font-weight:700;">不想被频繁打扰？</p>
  <p style="margin:0 0 12px; font-size:13px; line-height:1.7; color:{_EMAIL_MUTED};">
    你可以直接暂停 24 小时、去门户调整提醒，或者一键退订邮件通知。
  </p>
  <div>{actions_html}</div>
</td></tr></table>"""

    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<style>
  :root {{
    color-scheme: light dark;
    supported-color-schemes: light dark;
  }}
  @media (prefers-color-scheme: dark) {{
    body, .notify-page {{
      background: #111214 !important;
      color: #f5f5f7 !important;
    }}
    .notify-card {{
      background: #1c1d20 !important;
      box-shadow: 0 18px 48px rgba(0,0,0,0.34) !important;
    }}
    .notify-header {{
      background: linear-gradient(180deg, #24262b 0%, #1c1d20 100%) !important;
      border-bottom-color: #34363d !important;
    }}
    .notify-footer {{
      border-top-color: #34363d !important;
      color: #a1a1aa !important;
    }}
    .notify-info-panel,
    .notify-manage-panel {{
      background: #24262b !important;
      border-color: #34363d !important;
    }}
    .notify-primary-btn {{
      background: #f5f5f7 !important;
      color: #111214 !important;
    }}
    a {{
      color: #7ab8ff !important;
    }}
  }}
</style>
</head>
<body style="margin:0; padding:0; background:{_EMAIL_BG};
             font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',Roboto,Helvetica,Arial,sans-serif;
             -webkit-font-smoothing:antialiased; color:{_EMAIL_TEXT};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notify-page" style="background:{_EMAIL_BG};">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="notify-card" style="max-width:560px; background:{_EMAIL_CARD_BG};
       border-radius:28px; overflow:hidden; box-shadow:0 18px 48px rgba(15,23,42,0.07);">
<tr><td class="notify-header" style="padding:34px 28px 22px; text-align:left; background:linear-gradient(180deg, #fafcff 0%, #ffffff 100%); border-bottom:1px solid {_EMAIL_HAIRLINE};">
  <p style="margin:0 0 10px; color:{_EMAIL_MUTED}; font-size:12px; font-weight:700; letter-spacing:0.12em;">课程更新</p>
  <h1 style="margin:0; color:{_EMAIL_TEXT}; font-size:28px; font-weight:700; line-height:1.28; letter-spacing:-0.02em;">{heading}</h1>
  <p style="margin:10px 0 0; color:{_EMAIL_MUTED}; font-size:14px;">本封邮件共整理 {len(courses)} 门课程</p>
</td></tr>
<tr><td style="padding:28px 28px 26px;">
<p style="margin:0 0 8px; font-size:20px; line-height:1.55; color:{_EMAIL_TEXT}; font-weight:700;">这封邮件想告诉你什么</p>
<p style="margin:0 0 14px; font-size:15px; line-height:1.7; color:{_EMAIL_TEXT};">{intro}</p>
{next_action_html}
{portal_button_html}
{reason_html}
{manage_notice_html}
<p style="margin:2px 0 14px; font-size:17px; line-height:1.6; color:{_EMAIL_TEXT}; font-weight:700;">课程详情</p>
{cards_html}
</td></tr>
<tr><td class="notify-footer" style="padding:16px 24px; border-top:1px solid #f0f0f0; text-align:center; font-size:12px; color:{_EMAIL_MUTED};">
  BUAA \u535a\u96c5\u8bfe\u7a0b\u63a8\u9001{unsub_link}
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def _filter_for_subscriber(courses: list, sub) -> list:
    """根据订阅者偏好过滤课程"""
    result = []
    for c in courses:
        if is_course_expired(c):
            continue
        # 校区过滤
        if sub.campus_filter and sub.campus_filter not in (c.campus or ""):
            continue
        # 自主签到过滤
        if sub.self_sign_only:
            if not is_self_check_in(c):
                continue
        # 类别过滤
        sub_cats = sub.categories
        if sub_cats and c.category not in sub_cats:
            continue
        result.append(c)
    return result


def _dedupe_cutoff(event_type: str, delivery_mode: str):
    now = business_now()
    if event_type == "snipe":
        minutes = max(15, int(os.getenv("SNIPE_DEDUPE_MINUTES", "60")))
        return now - timedelta(minutes=minutes)
    if delivery_mode == "active_watch":
        minutes = max(60, int(os.getenv("ACTIVE_ENROLL_DEDUPE_MINUTES", "480")))
        return now - timedelta(minutes=minutes)
    if delivery_mode == "priority":
        return now - timedelta(hours=2)
    return None


def _extract_remaining_from_message(message: str) -> int | None:
    if not message:
        return None
    m = re.search(r"remaining=(\d+)", message)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _filter_by_recent_signal(session, courses: list, sub, event_type: str, delivery_mode: str) -> list:
    from src.models import NotificationEvent

    if not courses:
        return []

    course_map = {course.id: course for course in courses}
    latest_events = (
        session.query(NotificationEvent)
        .filter(NotificationEvent.subscriber_id == sub.id)
        .filter(NotificationEvent.channel == "email")
        .filter(NotificationEvent.success == True)  # noqa: E712
        .filter(NotificationEvent.course_id.in_(list(course_map.keys())))
        .order_by(NotificationEvent.sent_at.desc())
        .all()
    )

    latest_by_course = {}
    for event in latest_events:
        latest_by_course.setdefault(event.course_id, event)

    now = business_now()
    snipe_delta = max(1, int(os.getenv("SNIPE_RESEND_DELTA", "2")))
    active_delta = max(2, int(os.getenv("ACTIVE_ENROLL_RESEND_DELTA", "3")))
    active_cooldown = max(60, int(os.getenv("ACTIVE_ENROLL_DEDUPE_MINUTES", "480")))
    snipe_cooldown = max(15, int(os.getenv("SNIPE_DEDUPE_MINUTES", "60")))
    result = []

    for course in courses:
        latest = latest_by_course.get(course.id)
        if latest is None:
            result.append(course)
            continue

        elapsed_minutes = max(0, int((now - latest.sent_at).total_seconds() / 60)) if latest.sent_at else 10**9
        last_remaining = _extract_remaining_from_message(latest.message)
        current_remaining = max(0, int(getattr(course, "remaining", 0) or 0))

        if event_type == "snipe":
            if elapsed_minutes >= snipe_cooldown:
                result.append(course)
                continue
            if last_remaining is not None and current_remaining >= last_remaining + snipe_delta:
                result.append(course)
            continue

        if delivery_mode == "active_watch":
            if elapsed_minutes >= active_cooldown:
                result.append(course)
                continue
            if last_remaining is not None and current_remaining >= last_remaining + active_delta:
                result.append(course)
            continue

        result.append(course)

    return result


def _filter_unsent_for_subscriber(session, courses: list, sub, event_type: str, delivery_mode: str) -> list:
    from src.models import NotificationEvent

    if not courses:
        return []

    course_map = {course.id: course for course in courses}
    query = (
        session.query(NotificationEvent.course_id)
        .filter(NotificationEvent.subscriber_id == sub.id)
        .filter(NotificationEvent.channel == "email")
        .filter(NotificationEvent.success == True)  # noqa: E712
        .filter(NotificationEvent.course_id.in_(list(course_map.keys())))
    )

    cutoff = _dedupe_cutoff(event_type, delivery_mode)
    if cutoff is not None:
        query = query.filter(NotificationEvent.sent_at >= cutoff)
    else:
        query = query.filter(NotificationEvent.event_type == event_type)

    sent_ids = {row[0] for row in query.all()}
    pending = [course for course in courses if course.id not in sent_ids]

    # 对“退选补位”和“已开选监控”启用更细粒度的名额变化策略，
    # 允许在名额明显回升时突破冷却窗口，但避免稳定小波动反复打扰。
    if event_type == "snipe" or delivery_mode == "active_watch":
        blocked = [course for course in courses if course.id in sent_ids]
        pending.extend(_filter_by_recent_signal(session, blocked, sub, event_type, delivery_mode))

    seen = set()
    result = []
    for course in pending:
        if course.id in seen:
            continue
        seen.add(course.id)
        result.append(course)
    return result


async def send_email_to_subscribers(
    courses: list,
    base_url: str = "",
    event_type: str = "new",
    delivery_mode: str = "instant",
) -> int:
    """创建并投递课程邮件任务，返回成功投递的订阅者数量。"""
    from src.models import EmailSubscriber, get_session

    base_url = (base_url or os.getenv("APP_PUBLIC_BASE_URL") or "https://buaaboya.top").rstrip("/")
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

        queued_jobs = []
        now = business_now()
        for sub in subs:
            # 检查用户是否已暂停推送
            paused_until = getattr(sub, "push_paused_until", None)
            if paused_until and now < paused_until:
                logger.info(f"推送已暂停，跳过: {_mask_email(sub.email)} (暂停至 {paused_until.strftime('%Y-%m-%d %H:%M')})")
                continue

            filtered = _filter_for_subscriber(courses, sub)
            filtered = _filter_unsent_for_subscriber(session, filtered, sub, event_type, delivery_mode)
            if not filtered:
                continue

            dedupe_material = ";".join(
                sorted(f"{course.id}:{getattr(course, 'remaining', '')}" for course in filtered)
            )
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=sub.id,
                subscriber_email=sub.email,
                course_ids=[course.id for course in filtered],
                event_type=event_type,
                delivery_mode=delivery_mode,
                priority=100 if delivery_mode == "priority" else 50,
                payload={"base_url": base_url},
                dedupe_material=dedupe_material,
            )
            queued_jobs.append(job)

        session.commit()
        if not queued_jobs:
            return 0

        delivery = await drain_notification_jobs(
            {"email": deliver_email_notification_job},
            limit=len(queued_jobs),
        )
        return int(delivery["delivered_count"])
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def deliver_email_notification_job(job) -> NotificationDeliveryResult:
    """投递一条已持久化的邮件任务；重启后由同一处理器继续执行。"""
    from src.models import Course, EmailSubscriber, NotificationEvent, get_session

    session = get_session()
    try:
        course_ids = job.course_ids
        if not course_ids:
            return NotificationDeliveryResult(True, message="任务没有课程")

        subscriber = (
            session.query(EmailSubscriber)
            .filter(EmailSubscriber.id == job.subscriber_id)
            .first()
        )
        if not subscriber or not subscriber.verified or not subscriber.active:
            return NotificationDeliveryResult(True, message="订阅者已停用，跳过投递")

        now = business_now()
        if subscriber.push_paused_until and now < subscriber.push_paused_until:
            return NotificationDeliveryResult(True, message="订阅者处于暂停期，跳过投递")

        course_map = {
            course.id: course
            for course in session.query(Course).filter(Course.id.in_(course_ids)).all()
        }
        courses = [course_map[course_id] for course_id in course_ids if course_id in course_map]
        courses = [course for course in courses if not is_course_expired(course, now)]
        if not courses:
            return NotificationDeliveryResult(True, message="课程已不可用，跳过投递")

        successful_ids = {
            row[0]
            for row in (
                session.query(NotificationEvent.course_id)
                .filter(NotificationEvent.subscriber_id == subscriber.id)
                .filter(NotificationEvent.channel == "email")
                .filter(NotificationEvent.event_type == job.event_type)
                .filter(NotificationEvent.success.is_(True))
                .filter(NotificationEvent.course_id.in_([course.id for course in courses]))
                .all()
            )
        }
        courses = [course for course in courses if course.id not in successful_ids]
        if not courses:
            return NotificationDeliveryResult(True, message="已有成功投递记录，跳过重复发送")

        # 发送前再次应用用户偏好，避免任务排队期间的设置变更被忽略。
        courses = _filter_for_subscriber(courses, subscriber)
        if not courses:
            return NotificationDeliveryResult(True, message="用户偏好已变化，跳过投递")

        payload = job.payload
        base_url = (payload.get("base_url") or os.getenv("APP_PUBLIC_BASE_URL") or "https://buaaboya.top").rstrip("/")
        unsub_url = f"{base_url}/api/unsubscribe/{subscriber.token}" if base_url else ""
        pause_url = f"{base_url}/api/pause/{subscriber.token}?hours=24" if base_url else ""
        html = _build_notification_html(
            courses,
            unsub_url,
            pause_url,
            sub_token=subscriber.token,
            base_url=base_url,
            event_type=job.event_type,
            delivery_mode=job.delivery_mode,
            subscriber=subscriber,
        )
        subject = _build_notification_subject(job.event_type, job.delivery_mode, len(courses))
        ok = _send_raw_email(subscriber.email, subject, html, from_kind="notify")

        for course in courses:
            session.add(
                NotificationEvent(
                    subscriber_id=subscriber.id,
                    subscriber_email=subscriber.email,
                    course_id=course.id,
                    course_name=course.name,
                    course_category=getattr(course, "category", "") or "",
                    event_type=job.event_type,
                    delivery_mode=job.delivery_mode,
                    channel="email",
                    success=ok,
                    message=(
                        f"attempt={job.attempts};matched={len(courses)};"
                        f"remaining={getattr(course, 'remaining', '')}"
                    ),
                )
            )
        session.commit()

        if ok:
            logger.info(
                f"邮件推送成功: {len(courses)} 门课程 -> {_mask_email(subscriber.email)}"
            )
            return NotificationDeliveryResult(True, delivered_count=1, message="邮件已发送")

        logger.warning(f"邮件推送失败: {_mask_email(subscriber.email)}")
        return NotificationDeliveryResult(False, message="邮件发送失败")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def send_email_notification(
    courses: list,
    event_type: str = "new",
    delivery_mode: str = "instant",
) -> int:
    """Compatibility wrapper for subscriber email pushes."""
    return await send_email_to_subscribers(courses, event_type=event_type, delivery_mode=delivery_mode)


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
        course_name = _html(getattr(course, "name", ""))
        course_location = _html(getattr(course, "location", ""))
        safe_message = _html(message)
        body = f"""
<p style="font-size:15px; line-height:1.6; margin:0 0 12px;">
  <strong>课程：</strong>{course_name}<br>
  <strong>时间：</strong>{course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}<br>
  <strong>地点：</strong>{course_location}
</p>
{f'<p style="font-size:14px; color:{_EMAIL_MUTED};">备注：{safe_message}</p>' if message else ''}"""
        html = _email_shell(f"自动选课{status_label}", body)

        sent = 0
        for sub in subs:
            if _send_raw_email(sub.email, _email_subject_text(f"选课{status_label}: {getattr(course, 'name', '')}"), html, from_kind="notify"):
                sent += 1
        return sent > 0
    finally:
        session.close()


# ========== 选课提醒 ==========

def send_enroll_reminder_email(to_email: str, course) -> bool:
    """发送选课即将开始提醒"""
    enroll_str = course.enroll_start.strftime('%Y-%m-%d %H:%M') if course.enroll_start else '即将'
    course_category = _html(getattr(course, "category", ""))
    course_teacher = _html(getattr(course, "teacher", ""))
    course_campus = _html(getattr(course, "campus", ""))
    body = f"""
<p style="margin:0 0 14px; font-size:15px; line-height:1.7; color:{_EMAIL_TEXT};">
  你订阅的课程即将开放选课，建议现在就打开门户，确认网络和登录状态。
</p>
{_email_info_panel("选课提醒", getattr(course, "name", ""), f"{course_category} · {course_teacher} · {course_campus}<br>选课开始：<span style='font-weight:700; color:{_EMAIL_ACCENT};'>{enroll_str}</span>")}
<p style="margin:0; font-size:13px; color:{_EMAIL_MUTED}; line-height:1.7;">
  建议提前 1 到 2 分钟进入系统，避免临近开始时临时登录影响操作。
    </p>"""
    html = _email_shell("选课即将开始", body, eyebrow="选课提醒")
    return _send_raw_email(to_email, _email_subject_text(f"选课提醒：{getattr(course, 'name', '')}"), html, from_kind="reminder")
