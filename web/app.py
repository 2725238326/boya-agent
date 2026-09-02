"""
Flask Web 控制台
提供筛选配置、课程查看、自动选课开关、系统状态等功能
"""

import os
import asyncio
import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from functools import lru_cache
from flask import Flask, render_template, jsonify, request, Response, redirect, make_response, url_for
from flask_cors import CORS
from loguru import logger
from sqlalchemy import func, or_, text
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from web.qrcode_feature import qrcode_bp
from src.models import (
    Course,
    FilterConfig,
    PushLog,
    EnrollLog,
    EmailSubscriber,
    LoginBridgeTicket,
    EmailAuthChallenge,
    CourseReminder,
    NotificationEvent,
    get_session,
    init_db,
    commit_with_retry,
    is_database_locked_error,
)
from src.course_state import (
    HOT_COURSE_FILL_RATIO,
    HOT_COURSE_REMAINING_THRESHOLD,
    is_course_expired,
    is_enrollment_open,
    is_hot_course,
)
from src.push.rss_feed import generate_rss_feed, generate_atom_feed
from src.time_utils import now as business_now
from src.scheduler import (
    BROWSER_HARD_MAX_SCRAPE_RUNS,
    BROWSER_MAX_SCRAPE_RUNS,
    get_run_status,
    queue_scrape_task,
    run_scrape_task,
    submit_coroutine,
    update_scheduler_interval,
    update_daily_summary_schedule,
)
from web.security import (
    admin_error_response,
    is_admin_authorized,
    is_same_origin_request,
    mask_email,
    requires_admin,
    security_headers,
)


app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static"),
)
WEB_SECRET_KEY = (os.getenv("WEB_SECRET_KEY") or "").strip()
if len(WEB_SECRET_KEY) < 32 or WEB_SECRET_KEY.lower().startswith("replace-with-"):
    raise RuntimeError("WEB_SECRET_KEY must be configured with a non-example value of at least 32 characters")
app.secret_key = WEB_SECRET_KEY


@lru_cache(maxsize=256)
def _static_asset_version(filename: str) -> str:
    """Return a stable content hint so browsers can cache versioned assets safely."""
    try:
        stat = os.stat(os.path.join(app.static_folder, filename))
    except (OSError, TypeError):
        return "1"
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


@app.template_global("static_asset")
def static_asset(filename: str) -> str:
    """Build a cache-busting URL for a checked-in static asset."""
    return url_for("static", filename=filename, v=_static_asset_version(filename))

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
)

_configured_origins = {
    value.strip().rstrip("/")
    for value in (os.getenv("APP_ALLOWED_ORIGINS") or "").split(",")
    if value.strip()
}
if _configured_origins:
    CORS(app, origins=sorted(_configured_origins), supports_credentials=False)

PORTAL_SESSION_COOKIE = "portal_token"
PORTAL_SESSION_MAX_AGE = 60 * 60 * 24 * 180  # 180 days
LOGIN_EMAIL_COOLDOWN_SECONDS = 20
LOGIN_IP_COOLDOWN_SECONDS = max(2, int(os.getenv("LOGIN_IP_COOLDOWN_SECONDS", "5")))
LOGIN_BRIDGE_TTL_SECONDS = max(60, int(os.getenv("LOGIN_BRIDGE_TTL_SECONDS", "900")))
VERIFICATION_CODE_TTL_MINUTES = max(5, int(os.getenv("VERIFICATION_CODE_TTL_MINUTES", "20")))
AUTH_CHALLENGE_TTL_SECONDS = max(300, int(os.getenv("AUTH_CHALLENGE_TTL_SECONDS", "900")))
AUTH_CODE_MAX_ATTEMPTS = max(3, int(os.getenv("AUTH_CODE_MAX_ATTEMPTS", "5")))
VERIFICATION_CODE_LENGTH = 6
_login_email_last_sent_at = {}
_login_ip_last_sent_at = {}
DEFAULT_PUBLIC_BASE_URL = "https://buaaboya.top"
PUBLIC_API_CACHE_CONTROL = "public, max-age=15, stale-while-revalidate=30"
CATEGORY_CACHE_CONTROL = "public, max-age=300, stale-while-revalidate=600"

app.config["PORTAL_SESSION_COOKIE"] = PORTAL_SESSION_COOKIE
app.config["MAX_CONTENT_LENGTH"] = max(
    1024 * 1024,
    int(os.getenv("QRCODE_MAX_FILE_SIZE", str(5 * 1024 * 1024))) + 1024 * 1024,
)
app.register_blueprint(qrcode_bp)


@app.before_request
def _security_boundary():
    """在进入视图前统一执行管理端认证和跨站写请求检查。"""
    if requires_admin(request.path) and not is_admin_authorized():
        return admin_error_response()

    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not is_same_origin_request():
        return jsonify({
            "success": False,
            "code": "cross_origin_forbidden",
            "error": "请求来源不受信任",
        }), 403


@app.after_request
def _security_headers(response):
    return security_headers(response)


@app.errorhandler(RequestEntityTooLarge)
def _request_entity_too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "code": "request_too_large",
            "error": "上传内容超过大小限制",
        }), 413
    return "请求内容超过大小限制", 413


def _is_https_request() -> bool:
    proto = (request.headers.get("X-Forwarded-Proto") or "").lower()
    return request.is_secure or proto == "https"


def _set_portal_session_cookie(resp, token: str):
    resp.set_cookie(
        PORTAL_SESSION_COOKIE,
        token,
        max_age=PORTAL_SESSION_MAX_AGE,
        httponly=True,
        secure=_is_https_request(),
        samesite="Lax",
    )
    return resp


def _clear_portal_session_cookie(resp):
    resp.set_cookie(
        PORTAL_SESSION_COOKIE,
        "",
        expires=0,
        max_age=0,
        httponly=True,
        secure=_is_https_request(),
        samesite="Lax",
    )
    return resp


def _get_session_token() -> str:
    return (request.cookies.get(PORTAL_SESSION_COOKIE) or "").strip()


def _get_public_base_url() -> str:
    """
    获取对外可访问基址。优先使用 APP_PUBLIC_BASE_URL，避免生成 127.0.0.1 链接。
    """
    configured = (os.getenv("APP_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    return DEFAULT_PUBLIC_BASE_URL


def _normalize_email(value) -> str:
    """Normalize a user-supplied email and reject header-injection characters."""
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    if len(normalized) > 254 or normalized.count("@") != 1:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        return ""
    if any(char in '<>,;:"()[]\\' for char in normalized):
        return ""
    local, domain = normalized.split("@", 1)
    if not local or not domain or any(char.isspace() for char in normalized):
        return ""
    return normalized


def _check_login_email_cooldown(email: str) -> int:
    """按邮箱和来源 IP 返回剩余冷却秒数；0 表示可发送。"""
    now = time.monotonic()
    email_last = _login_email_last_sent_at.get(email)
    ip_key = request.remote_addr or "unknown"
    ip_last = _login_ip_last_sent_at.get(ip_key)
    email_remain = (
        int(LOGIN_EMAIL_COOLDOWN_SECONDS - (now - email_last))
        if email_last is not None
        else 0
    )
    ip_remain = (
        int(LOGIN_IP_COOLDOWN_SECONDS - (now - ip_last))
        if ip_last is not None
        else 0
    )
    return max(0, email_remain, ip_remain)


def _mark_login_email_sent(email: str):
    now = time.monotonic()
    _login_email_last_sent_at[email] = now
    _login_ip_last_sent_at[request.remote_addr or "unknown"] = now


def _database_busy_message(action: str) -> str:
    return f"当前{action}的人较多，请稍后再试"


def _database_busy_json(action: str):
    return jsonify({"success": False, "error": _database_busy_message(action)}), 503


def _create_login_bridge_ticket(session, sub: EmailSubscriber) -> LoginBridgeTicket:
    expires_at = business_now() + timedelta(seconds=LOGIN_BRIDGE_TTL_SECONDS)
    bridge = LoginBridgeTicket(
        subscriber_id=sub.id,
        # 旧字段保留用于数据库兼容；新桥接记录不再复制邮箱或会话令牌。
        subscriber_email="",
        subscriber_token="",
        expires_at=expires_at,
    )
    session.add(bridge)
    session.flush()
    return bridge


def _mark_bridge_verified(session, ticket: str, sub: EmailSubscriber) -> None:
    ticket = (ticket or "").strip()
    if not ticket:
        return
    now = business_now()
    bridge = session.query(LoginBridgeTicket).filter_by(ticket=ticket).first()
    if not bridge:
        return
    if bridge.subscriber_id != sub.id:
        return
    if bridge.expires_at and bridge.expires_at <= now:
        return
    if bridge.claimed_at:
        return
    bridge.verified = True
    bridge.verified_at = now


def _bridge_payload(bridge: LoginBridgeTicket) -> dict:
    expires_in = int((bridge.expires_at - business_now()).total_seconds())
    return {
        "bridge_ticket": bridge.ticket,
        "bridge_expires_in": max(0, expires_in),
    }


def _portal_url_for_subscriber(sub: EmailSubscriber) -> str:
    return "/portal"


def _generate_verification_code() -> str:
    return f"{secrets.randbelow(10 ** VERIFICATION_CODE_LENGTH):0{VERIFICATION_CODE_LENGTH}d}"


def _normalize_verification_code(text: str) -> str:
    return "".join(ch for ch in (text or "") if ch.isdigit())[:VERIFICATION_CODE_LENGTH]


def _hash_one_time_code(code: str) -> str:
    """用应用密钥保存验证码摘要，避免数据库直接保存可用验证码。"""
    return hmac.new(
        WEB_SECRET_KEY.encode("utf-8"),
        (code or "").encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _issue_verification_code(sub: EmailSubscriber) -> str:
    code = _generate_verification_code()
    sub.verify_code = _hash_one_time_code(code)
    sub.verify_code_attempts = 0
    sub.verify_code_expires_at = business_now() + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
    return code


def _clear_verification_code(sub: EmailSubscriber) -> None:
    sub.verify_code = None
    sub.verify_code_expires_at = None
    sub.verify_code_attempts = 0


def _issue_login_code(sub: EmailSubscriber) -> str:
    code = _generate_verification_code()
    sub.login_code = _hash_one_time_code(code)
    sub.login_code_attempts = 0
    sub.login_code_expires_at = business_now() + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
    return code


def _clear_login_code(sub: EmailSubscriber) -> None:
    sub.login_code = None
    sub.login_code_expires_at = None
    sub.login_code_attempts = 0


def _hash_auth_challenge(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _create_auth_challenge(
    session,
    sub: EmailSubscriber,
    purpose: str,
    bridge_ticket: str = "",
) -> tuple[str, EmailAuthChallenge]:
    raw_token = secrets.token_urlsafe(32)
    challenge = EmailAuthChallenge(
        token_hash=_hash_auth_challenge(raw_token),
        subscriber_id=sub.id,
        purpose=purpose,
        bridge_ticket=(bridge_ticket or "").strip() or None,
        expires_at=business_now() + timedelta(seconds=AUTH_CHALLENGE_TTL_SECONDS),
    )
    session.add(challenge)
    session.flush()
    return raw_token, challenge


def _load_auth_challenge(session, raw_token: str, purpose: str):
    if not raw_token:
        return None
    return (
        session.query(EmailAuthChallenge)
        .filter_by(token_hash=_hash_auth_challenge(raw_token), purpose=purpose)
        .first()
    )


def _consume_auth_challenge(session, raw_token: str, purpose: str):
    """消费短期一次性链接；数据库只按摘要查找原始链接。"""
    challenge = _load_auth_challenge(session, raw_token, purpose)
    if not challenge:
        return None, "invalid"

    now = business_now()
    if challenge.used_at:
        return None, "used"
    if challenge.expires_at and challenge.expires_at <= now:
        return None, "expired"

    sub = (
        session.query(EmailSubscriber)
        .filter_by(id=challenge.subscriber_id)
        .first()
    )
    if not sub:
        return None, "invalid"

    # 用条件更新把“检查未使用 + 标记已使用”收敛为一次数据库写入，
    # 避免两个并发请求同时消费同一条邮件链接。
    marked = (
        session.query(EmailAuthChallenge)
        .filter(
            EmailAuthChallenge.id == challenge.id,
            EmailAuthChallenge.used_at.is_(None),
        )
        .update({EmailAuthChallenge.used_at: now}, synchronize_session=False)
    )
    if marked != 1:
        return None, "used"

    if purpose == "verify":
        sub.verified = True
        sub.active = True
        _clear_verification_code(sub)
    elif not (sub.verified and sub.active):
        return None, "invalid"

    _mark_bridge_verified(session, challenge.bridge_ticket or "", sub)
    return sub, None


def _record_code_failure(sub: EmailSubscriber, purpose: str) -> str:
    if purpose == "login":
        attempts = int(sub.login_code_attempts or 0) + 1
        sub.login_code_attempts = attempts
        if attempts >= AUTH_CODE_MAX_ATTEMPTS:
            _clear_login_code(sub)
            return "too_many_attempts"
    else:
        attempts = int(sub.verify_code_attempts or 0) + 1
        sub.verify_code_attempts = attempts
        if attempts >= AUTH_CODE_MAX_ATTEMPTS:
            _clear_verification_code(sub)
            return "too_many_attempts"
    return "code_mismatch"


def _verify_subscriber_code(session, email: str, code: str, bridge_ticket: str = ""):
    sub = session.query(EmailSubscriber).filter_by(email=email).first()
    if not sub:
        return None, "missing"

    normalized = _normalize_verification_code(code)
    if len(normalized) != VERIFICATION_CODE_LENGTH:
        purpose = "login" if sub.verified else "verify"
        return None, _record_code_failure(sub, purpose)

    purpose = "login" if sub.verified else "verify"
    expected = sub.login_code if purpose == "login" else sub.verify_code
    expires_at = sub.login_code_expires_at if purpose == "login" else sub.verify_code_expires_at
    if not expected or not expires_at:
        return None, "missing_code"
    now = business_now()
    if expires_at <= now:
        return None, "expired_code"
    if not hmac.compare_digest(expected, _hash_one_time_code(normalized)):
        return None, _record_code_failure(sub, purpose)

    if purpose == "login":
        # 把“仍是这条验证码 + 尚未过期 + 清空验证码”合并为条件更新，
        # 避免两个并发请求同时使用同一条验证码。
        consumed = (
            session.query(EmailSubscriber)
            .filter(
                EmailSubscriber.id == sub.id,
                EmailSubscriber.login_code == expected,
                EmailSubscriber.login_code_expires_at > now,
            )
            .update({
                EmailSubscriber.login_code: None,
                EmailSubscriber.login_code_expires_at: None,
                EmailSubscriber.login_code_attempts: 0,
            }, synchronize_session=False)
        )
        if consumed != 1:
            return None, "code_mismatch"
        _clear_login_code(sub)
    else:
        consumed = (
            session.query(EmailSubscriber)
            .filter(
                EmailSubscriber.id == sub.id,
                EmailSubscriber.verify_code == expected,
                EmailSubscriber.verify_code_expires_at > now,
            )
            .update({
                EmailSubscriber.verified: True,
                EmailSubscriber.active: True,
                EmailSubscriber.verify_code: None,
                EmailSubscriber.verify_code_expires_at: None,
                EmailSubscriber.verify_code_attempts: 0,
            }, synchronize_session=False)
        )
        if consumed != 1:
            return None, "code_mismatch"
        sub.verified = True
        sub.active = True
        _clear_verification_code(sub)
    _mark_bridge_verified(session, bridge_ticket, sub)
    return sub, None


def _current_subscriber(session):
    """只从 HttpOnly 会话 Cookie 解析当前用户，不接受 URL/JSON 中的身份字段。"""
    token = _get_session_token()
    if not token:
        return None
    return (
        session.query(EmailSubscriber)
        .filter_by(token=token, verified=True, active=True)
        .first()
    )


def _serialize_course_reminders(session, subscriber_id: int, pending_only: bool = False):
    query = (
        session.query(
            CourseReminder,
            Course.name,
            Course.category,
            Course.teacher,
            Course.enroll_start,
        )
        .outerjoin(Course, Course.id == CourseReminder.course_id)
        .filter(CourseReminder.subscriber_id == subscriber_id)
        .order_by(CourseReminder.created_at.desc())
    )
    if pending_only:
        query = query.filter(CourseReminder.sent.is_(False))

    result = []
    for reminder, course_name, course_category, course_teacher, enroll_start in query.all():
        result.append({
            "id": reminder.id,
            "course_id": reminder.course_id,
            "course_name": course_name or "未知课程",
            "course_category": course_category or "",
            "course_teacher": course_teacher or "",
            "enroll_start": enroll_start.strftime("%Y-%m-%d %H:%M") if enroll_start else "",
            "remind_before_minutes": reminder.remind_before_minutes,
            "sent": reminder.sent,
            "created_at": reminder.created_at.strftime("%Y-%m-%d %H:%M") if reminder.created_at else "",
        })
    return result


def _queue_scrape_task(mode: str, started_message: str, joined_message: str) -> dict:
    payload = queue_scrape_task(mode=mode)
    payload["message"] = joined_message if payload.get("joined_existing") else started_message
    return payload


def _cached_json(payload: dict, cache_control: str = PUBLIC_API_CACHE_CONTROL):
    """Return JSON with an explicit cache policy for public, short-lived data."""
    response = jsonify(payload)
    response.headers["Cache-Control"] = cache_control
    return response


# ========== 页面路由 ==========

@app.route("/")
def home_page():
    """公开首页"""
    return render_template("home.html")


@app.route("/healthz")
def healthz():
    """供 Nginx、systemd 和部署流程使用的公开健康探活端点。"""
    session = get_session()
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check failed")
        response = jsonify({"success": False, "status": "unavailable"})
        response.status_code = 503
    else:
        response = jsonify({"success": True, "status": "ok"})
    finally:
        session.close()
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/admin")
@app.route("/admin/")
def admin_console():
    """管理后台首页"""
    return render_template("index.html")


@app.route("/console")
def console_redirect():
    """历史控制台入口，统一跳转到 /admin"""
    return redirect("/admin")


@app.route("/subscribe")
def subscribe_page():
    """订阅页面（公开访问）"""
    return render_template("subscribe.html")


@app.route("/verify/<token>")
def verify_page(token):
    requested_purpose = (request.args.get("purpose") or "verify").strip().lower()
    purpose = "login" if requested_purpose == "login" else "verify"
    is_login = purpose == "login"
    session = get_session()
    try:
        challenge = _load_auth_challenge(session, token, purpose)
        now = business_now()
        if (
            not challenge
            or challenge.used_at
            or (challenge.expires_at and challenge.expires_at <= now)
            or not session.query(EmailSubscriber).filter_by(id=challenge.subscriber_id).first()
        ):
            return render_template(
                "verify.html",
                success=False,
                pending=False,
                portal_url="/subscribe",
                is_login=is_login,
                title="登录没有完成" if is_login else "验证没有完成",
                detail=(
                    "这个登录链接无效、已失效，或已经被系统清理。请回到订阅页重新发送一封新的登录邮件。"
                    if is_login
                    else "这个验证链接无效、已失效，或已经被系统清理。请回到订阅页重新发送一封新的验证邮件。"
                ),
                auto_redirect=False,
                button_label="返回订阅页",
            )

        # GET 只展示待确认页面，不消费挑战，避免邮箱安全扫描器提前使用一次性链接。
        return render_template(
            "verify.html",
            success=False,
            pending=True,
            confirm_endpoint=f"/api/{purpose}/{token}/confirm",
            is_login=is_login,
            portal_url="/subscribe",
            title="确认登录" if is_login else "确认验证邮箱",
            detail=(
                "点击下方按钮后，系统会验证这条一次性登录链接并打开课程门户。链接只能使用一次，并会在短时间后失效。"
                if is_login
                else "点击下方按钮后，系统会验证这条一次性验证链接并打开课程门户。链接只能使用一次，并会在短时间后失效。"
            ),
            auto_redirect=False,
            button_label="返回订阅页",
        )
    except Exception as e:
        session.rollback()
        logger.exception("verify page failed")
        if is_database_locked_error(e):
            logger.warning("验证页处理遇到数据库锁竞争")
            return render_template(
                "verify.html",
                success=False,
                portal_url="/subscribe",
                is_login=is_login,
                title="系统繁忙，请稍后重试",
                detail=(
                    "当前请求较多，系统正在排队处理。请稍后再试，或回到订阅页重新发送一封新的登录邮件。"
                    if is_login
                    else "当前验证请求较多，系统正在排队处理。请稍后再试，或回到订阅页重新发送一封新的验证邮件。"
                ),
                auto_redirect=False,
                button_label="返回订阅页",
            ), 503
        return render_template(
            "verify.html",
            success=False,
            portal_url="/subscribe",
            is_login=is_login,
            title="登录失败" if is_login else "验证失败",
            detail=(
                "系统处理这条登录链接时出了问题。请稍后再试，或回到订阅页重新发送一封新的登录邮件。"
                if is_login
                else "系统处理这条验证链接时出了问题。请稍后再试，或回到订阅页重新发送一封新的验证邮件。"
            ),
            auto_redirect=False,
            button_label="返回订阅页",
        ), 500
    finally:
        session.close()


# ========== API 路由 ==========

@app.route("/api/courses")
def api_courses():
    """获取课程列表"""
    session = get_session()
    try:
        query = session.query(Course).order_by(Course.first_seen.desc())
        now = business_now()

        category = request.args.get("category")
        campus = request.args.get("campus")
        self_sign = request.args.get("self_sign")
        keyword = request.args.get("keyword")
        include_expired = request.args.get("include_expired", "false").lower() == "true"
        today_new = request.args.get("today_new", "false").lower() == "true"
        available_now = request.args.get("available_now", "false").lower() == "true"
        waitlist_only = request.args.get("waitlist_only", "false").lower() == "true"
        if not include_expired:
            query = query.filter(Course.expired.is_(False))
        if today_new:
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            query = query.filter(Course.first_seen >= today_start)

        if category:
            query = query.filter(Course.category.contains(category))
        if campus:
            query = query.filter(Course.campus.contains(campus))
        if self_sign == "true":
            query = query.filter(
                Course.check_in_method.contains("自主")
                | Course.check_in_method.contains("自选")
                | Course.sign_method.contains("自主")
                | Course.sign_method.contains("自选")
            )
        if keyword:
            query = query.filter(Course.name.contains(keyword))
        if available_now:
            query = query.filter((Course.capacity - Course.enrolled) > 0)
        if waitlist_only:
            query = query.filter(Course.expired.is_(False)).filter((Course.capacity - Course.enrolled) <= 0)

        courses = query.limit(500).all()
        if not include_expired:
            courses = [course for course in courses if not is_course_expired(course, now)]
        if available_now:
            courses = [course for course in courses if is_enrollment_open(course, now)]
        courses = courses[:200]
        return _cached_json({
            "success": True,
            "data": [c.to_dict() for c in courses],
            "total": len(courses),
        })
    except Exception:
        logger.exception("加载课程列表失败")
        return jsonify({"success": False, "code": "courses_unavailable", "error": "课程加载失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/public/insights")
def api_public_insights():
    """订阅页公开洞察：可选课程数、热门课程、最近开抢倒计时"""
    now = business_now()
    session = get_session()
    try:
        active_candidates = (
            session.query(Course)
            .filter(Course.expired.is_(False))
            .filter(or_(Course.end_time.is_(None), Course.end_time > now))
            .filter(or_(Course.enroll_end.is_(None), Course.enroll_end > now))
            .all()
        )
        active_courses = [course for course in active_candidates if not is_course_expired(course, now)]
        available = [
            c for c in active_courses
            if c.remaining > 0 and is_enrollment_open(c, now)
        ]

        hot_since = now - timedelta(hours=48)
        hot_rows = (
            session.query(
                NotificationEvent.course_id,
                func.count(NotificationEvent.id).label("cnt"),
            )
            .filter(NotificationEvent.sent_at >= hot_since)
            .group_by(NotificationEvent.course_id)
            .all()
        )
        hot_count_map = {row.course_id: int(row.cnt or 0) for row in hot_rows}

        def _hot_score(c):
            # 名额压力：剩余越少越热（0~60）
            pressure = (1 - (c.remaining / max(c.capacity, 1))) * 60

            # 开抢临近：越接近开抢越热（0~30）
            urgency = 0.0
            if c.enroll_start:
                seconds_left = (c.enroll_start - now).total_seconds()
                if seconds_left <= 0:
                    urgency = 30
                else:
                    urgency = max(0.0, (1 - min(seconds_left, 48 * 3600) / (48 * 3600)) * 30)

            # 近期推送热度：最近 48h 的通知次数（上限 10 分）
            heat = min(10.0, hot_count_map.get(c.id, 0) * 1.5)
            return round(pressure + urgency + heat, 2)

        popular_sorted = sorted(available, key=lambda c: _hot_score(c), reverse=True)[:3]
        popular_courses = [{
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "campus": c.campus,
            "remaining": c.remaining,
            "capacity": c.capacity,
            "hot_score": _hot_score(c),
            "recent_push_count": hot_count_map.get(c.id, 0),
        } for c in popular_sorted]

        upcoming = [
            c for c in available
            if c.enroll_start and c.enroll_start > now
        ]
        upcoming.sort(key=lambda c: c.enroll_start)
        next_course = upcoming[0] if upcoming else None

        next_enroll = None
        if next_course:
            delta = next_course.enroll_start - now
            next_enroll = {
                "course_id": next_course.id,
                "course_name": next_course.name,
                "enroll_start": next_course.enroll_start.strftime("%Y-%m-%d %H:%M:%S"),
                "seconds_left": max(0, int(delta.total_seconds())),
            }

        return _cached_json({
            "success": True,
            "data": {
                "available_count": len(available),
                "active_count": len(active_courses),
                "popular_courses": popular_courses,
                "next_enroll": next_enroll,
                "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
        })
    finally:
        session.close()


@app.route("/api/config", methods=["GET"])
def api_get_config():
    """获取筛选配置"""
    session = get_session()
    try:
        config = session.query(FilterConfig).first()
        if not config:
            config = FilterConfig(id=1)
            session.add(config)
            session.commit()
        return jsonify({"success": True, "data": config.to_dict()})
    finally:
        session.close()


@app.route("/api/config", methods=["PUT"])
def api_update_config():
    """更新筛选配置"""
    session = get_session()
    try:
        data = request.get_json(silent=True) or {}
        config = session.query(FilterConfig).first()
        if not config:
            config = FilterConfig(id=1)
            session.add(config)

        if "categories" in data:
            config.categories = data["categories"]
        if "self_sign_only" in data:
            config.self_sign_only = data["self_sign_only"]
        if "strict_boya_only" in data:
            config.strict_boya_only = data["strict_boya_only"]
        if "min_remaining" in data:
            config.min_remaining = int(data["min_remaining"])
        if "campus_filter" in data:
            config.campus_filter = data["campus_filter"]
        if "keyword_whitelist" in data:
            config.keyword_whitelist = data["keyword_whitelist"]
        if "keyword_blacklist" in data:
            config.keyword_blacklist = data["keyword_blacklist"]
        if "auto_enroll_enabled" in data:
            config.auto_enroll_enabled = data["auto_enroll_enabled"]
        if "priority_keywords" in data:
            config.priority_keywords = data["priority_keywords"]
        if "confirm_before_enroll" in data:
            config.confirm_before_enroll = data["confirm_before_enroll"]
        if "max_auto_enroll_per_day" in data:
            config.max_auto_enroll_per_day = int(data["max_auto_enroll_per_day"])
        if "telegram_enabled" in data:
            config.telegram_enabled = data["telegram_enabled"]
        if "email_enabled" in data:
            config.email_enabled = data["email_enabled"]
        if "rss_enabled" in data:
            config.rss_enabled = data["rss_enabled"]
        if "daily_summary_enabled" in data:
            config.daily_summary_enabled = data["daily_summary_enabled"]
        if "daily_summary_time" in data:
            config.daily_summary_time = str(data["daily_summary_time"]).strip()
        if "interval_minutes" in data:
            interval_minutes = int(data["interval_minutes"])
            if not 1 <= interval_minutes <= 1440:
                return jsonify({"success": False, "error": "抓取间隔必须在 1 到 1440 分钟之间"}), 400
            config.interval_minutes = interval_minutes
            update_scheduler_interval(config.interval_minutes)

        session.commit()
        if "daily_summary_enabled" in data or "daily_summary_time" in data:
            update_daily_summary_schedule()
        logger.info("配置已更新")
        return jsonify({"success": True, "message": "配置已保存"})
    except Exception:
        session.rollback()
        logger.exception("配置更新失败")
        return jsonify({"success": False, "error": "配置保存失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/enroll/toggle", methods=["POST"])
def api_toggle_enroll():
    """切换自动选课开关"""
    session = get_session()
    try:
        config = session.query(FilterConfig).first()
        config.auto_enroll_enabled = not config.auto_enroll_enabled
        session.commit()
        status = "已开启" if config.auto_enroll_enabled else "已关闭"
        return jsonify({
            "success": True,
            "enabled": config.auto_enroll_enabled,
            "message": f"自动选课{status}",
        })
    except Exception:
        session.rollback()
        logger.exception("自动选课开关更新失败")
        return jsonify({"success": False, "error": "自动选课开关更新失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/trigger", methods=["POST"])
def api_trigger_scrape():
    """Trigger a scrape and return immediately by default."""
    wait = (request.args.get("wait") or "").strip().lower() == "true"
    mode = (request.args.get("mode") or "full").strip().lower()
    if mode not in {"full", "quick"}:
        mode = "full"

    try:
        if not wait:
            payload = _queue_scrape_task(
                mode=mode,
                started_message="已开始后台抓取课程",
                joined_message="后台已有抓取任务，正在为你同步最新结果",
            )
            return jsonify(payload)

        future = submit_coroutine(run_scrape_task(mode=mode))
        result = future.result(timeout=180)
        if result.get("success"):
            return jsonify(result)
        return jsonify(result), 500
    except Exception:
        logger.exception("manual scrape trigger failed")
        return jsonify({"success": False, "error": "抓取任务启动失败，请稍后重试"}), 500


@app.route("/api/portal/refresh", methods=["POST"])
def api_portal_refresh():
    """Start or join a scrape for the user portal without blocking the request."""
    session = get_session()
    try:
        if not _current_subscriber(session):
            return jsonify({"success": False, "error": "未登录"}), 401
    finally:
        session.close()

    try:
        payload = _queue_scrape_task(
            mode="quick",
            started_message="已开始后台刷新课程",
            joined_message="后台已有刷新任务，正在为你同步最新结果",
        )
        return jsonify(payload)
    except Exception as e:
        logger.exception("portal refresh trigger failed")
        return jsonify({"success": False, "error": "刷新课程失败，请稍后重试"}), 500


@app.route("/api/status")
def api_status():
    """获取系统运行状态"""
    from src.scheduler import _browser_state, _push_buffer
    status = get_run_status()
    now = business_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    session = get_session()
    try:
        active_courses = (
            session.query(Course)
            .filter(Course.expired == False)  # noqa: E712
            .all()
        )
        active_courses = [course for course in active_courses if not is_course_expired(course, now)]
        status["total_courses_in_db"] = session.query(Course).count()
        status["total_available_courses"] = sum(1 for course in active_courses if course.remaining > 0)
        status["hot_watch_course_count"] = sum(1 for course in active_courses if is_hot_course(course, now))
        status["hot_course_fill_ratio"] = HOT_COURSE_FILL_RATIO
        status["hot_course_remaining_threshold"] = HOT_COURSE_REMAINING_THRESHOLD
        status["total_new_today"] = session.query(Course).filter(Course.first_seen >= today_start).count()
        status["total_delivered_today"] = (
            session.query(NotificationEvent)
            .filter(NotificationEvent.sent_at >= today_start)
            .filter(NotificationEvent.success == True)  # noqa: E712
            .filter(NotificationEvent.channel == "email")
            .count()
        )
        status["total_push_logs"] = session.query(PushLog).count()
        status["total_enroll_logs"] = session.query(EnrollLog).count()
    finally:
        session.close()

    # 浏览器和推送缓冲区状态
    browser_alive = False
    try:
        page = _browser_state.get("page")
        if page:
            _ = page.url
            browser_alive = True
    except Exception:
        pass

    status["browser_alive"] = browser_alive
    status["browser_scrape_runs"] = int(_browser_state.get("scrape_runs", 0))
    status["browser_recycle_threshold"] = BROWSER_MAX_SCRAPE_RUNS
    status["browser_hard_recycle_threshold"] = BROWSER_HARD_MAX_SCRAPE_RUNS
    status["push_buffer_urgent"] = len(_push_buffer.get("urgent", []))
    status["push_buffer_soon"] = len(_push_buffer.get("soon", []))

    return jsonify({"success": True, "data": status})


@app.route("/api/portal/refresh/status")
def api_portal_refresh_status():
    """为已登录门户提供不含后台统计的刷新状态。"""
    if not _get_session_token():
        return jsonify({"success": False, "error": "未登录"}), 401
    session = get_session()
    try:
        if not _current_subscriber(session):
            return jsonify({"success": False, "error": "会话失效"}), 401
    finally:
        session.close()

    status = get_run_status()
    return jsonify({
        "success": True,
        "data": {
            "is_running": status["is_running"],
            "last_run": status["last_run"],
            "last_success": status["last_success"],
        },
    })


@app.route("/api/categories")
def api_categories():
    """获取所有已知课程类别"""
    session = get_session()
    try:
        categories = (
            session.query(Course.category)
            .filter(Course.category.isnot(None))
            .distinct()
            .order_by(Course.category.asc())
            .all()
        )
        return _cached_json({
            "success": True,
            "data": [c[0] for c in categories if c[0]],
        }, CATEGORY_CACHE_CONTROL)
    finally:
        session.close()


@app.route("/api/logs/push")
def api_push_logs():
    """获取推送日志"""
    session = get_session()
    try:
        logs = (
            session.query(PushLog)
            .order_by(PushLog.pushed_at.desc())
            .limit(50)
            .all()
        )
        return jsonify({
            "success": True,
            "data": [{
                "id": l.id,
                "course_id": l.course_id,
                "push_type": l.push_type,
                "pushed_at": l.pushed_at.strftime("%Y-%m-%d %H:%M"),
                "success": l.success,
            } for l in logs],
        })
    finally:
        session.close()


@app.route("/api/logs/enroll")
def api_enroll_logs():
    """获取选课日志"""
    session = get_session()
    try:
        logs = (
            session.query(EnrollLog)
            .order_by(EnrollLog.attempted_at.desc())
            .limit(50)
            .all()
        )
        return jsonify({
            "success": True,
            "data": [{
                "id": l.id,
                "course_id": l.course_id,
                "course_name": l.course_name,
                "attempted_at": l.attempted_at.strftime("%Y-%m-%d %H:%M"),
                "success": l.success,
                "message": l.message,
            } for l in logs],
        })
    finally:
        session.close()


# ========== RSS 端点 ==========

def _public_feed_courses(session):
    config = session.query(FilterConfig).first()
    if config and not config.rss_enabled:
        return None
    courses = (
        session.query(Course)
        .filter(Course.expired == False)  # noqa: E712
        .order_by(Course.first_seen.desc())
        .limit(200)
        .all()
    )
    return [course for course in courses if not is_course_expired(course)]

@app.route("/rss")
def rss_feed():
    """RSS 2.0 Feed"""
    session = get_session()
    try:
        courses = _public_feed_courses(session)
        if courses is None:
            return Response("RSS 已关闭", status=404, mimetype="text/plain")
        base_url = _get_public_base_url()
        xml = generate_rss_feed(courses, base_url)
        response = Response(xml, mimetype="application/rss+xml; charset=utf-8")
        response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
        return response
    finally:
        session.close()


@app.route("/atom")
def atom_feed():
    """Atom Feed"""
    session = get_session()
    try:
        courses = _public_feed_courses(session)
        if courses is None:
            return Response("Atom 已关闭", status=404, mimetype="text/plain")
        base_url = _get_public_base_url()
        xml = generate_atom_feed(courses, base_url)
        response = Response(xml, mimetype="application/atom+xml; charset=utf-8")
        response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
        return response
    finally:
        session.close()


# ========== 邮件订阅 API ==========

@app.route("/api/subscribe", methods=["POST"])
def api_subscribe():
    """用户提交邮件订阅"""
    from src.push.email_push import send_login_code_email, send_verification_email

    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email"))
    if not email:
        return jsonify({"success": False, "error": "请输入有效的邮箱地址"}), 400

    if _check_login_email_cooldown(email):
        return jsonify({
            "success": True,
            "message": "如果这个邮箱可以使用，新的邮件已经在处理中；请稍候检查收件箱。",
            "data": {
                "email": email,
                "verify_code_required": True,
                "verify_code_length": VERIFICATION_CODE_LENGTH,
            },
        })

    _mark_login_email_sent(email)
    session = get_session()
    try:
        existing = session.query(EmailSubscriber).filter_by(email=email).first()
        if existing:
            if existing.active and existing.verified:
                login_code = _issue_login_code(existing)
                bridge = _create_login_bridge_ticket(session, existing)
                raw_challenge, _ = _create_auth_challenge(
                    session, existing, "login", bridge.ticket
                )
                base_url = _get_public_base_url()
                login_url = f"{base_url}/api/login/{raw_challenge}"
                subscribe_url = f"{base_url}/subscribe"
                ok = send_login_code_email(email, login_url, login_code, subscribe_url)
                if not ok:
                    session.rollback()
                    return jsonify({"success": False, "error": "邮件发送失败，请稍后重试"}), 502
                commit_with_retry(session)
                _mark_login_email_sent(email)
                return jsonify({
                    "success": True,
                    "message": "如果这个邮箱可以使用，邮件已经发出；请回到本页输入邮件里的验证码。",
                    "data": {
                        "email": email,
                        "verify_code_required": True,
                        "verify_code_length": VERIFICATION_CODE_LENGTH,
                        "code_expires_in_minutes": VERIFICATION_CODE_TTL_MINUTES,
                    },
                    **_bridge_payload(bridge),
                })

            # 未完成验证的记录可以重新发送验证邮件，但不会重置已有偏好。
            existing.active = True
            existing.verified = False
            existing.onboarding_seen_at = None
            sub = existing
        else:
            sub = EmailSubscriber(
                email=email,
                campus_filter="",
                self_sign_only=True,
            )
            sub.categories = []
            sub.onboarding_seen_at = None
            session.add(sub)

        session.flush()
        verify_code = _issue_verification_code(sub)
        bridge = _create_login_bridge_ticket(session, sub)
        raw_challenge, _ = _create_auth_challenge(session, sub, "verify", bridge.ticket)
        base_url = _get_public_base_url()
        verify_url = f"{base_url}/verify/{raw_challenge}"
        subscribe_url = f"{base_url}/subscribe"
        ok = send_verification_email(email, verify_url, verify_code, subscribe_url)

        if not ok:
            session.rollback()
            return jsonify({"success": False, "error": "验证邮件发送失败，请稍后重试"}), 502

        commit_with_retry(session)
        _mark_login_email_sent(email)
        return jsonify({
            "success": True,
            "message": "如果这个邮箱可以使用，邮件已经发出；请回到本页输入邮件里的验证码。",
            "data": {
                "email": email,
                "verify_code_required": True,
                "verify_code_length": VERIFICATION_CODE_LENGTH,
                "code_expires_in_minutes": VERIFICATION_CODE_TTL_MINUTES,
            },
            **_bridge_payload(bridge),
        })
    except Exception as e:
        session.rollback()
        logger.exception("订阅流程失败")
        if is_database_locked_error(e):
            return _database_busy_json("注册")
        return jsonify({"success": False, "error": "订阅处理失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/login/request", methods=["POST"])
def api_login_request():
    """为已验证用户发送一次性登录链接和验证码。"""
    from src.push.email_push import send_login_code_email

    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email"))
    if not email:
        return jsonify({"success": False, "error": "请输入有效的邮箱地址"}), 400

    generic_response = {
        "success": True,
        "message": "如果这个邮箱已完成验证，登录邮件已经发出；请检查收件箱，或回到本页输入验证码。",
        "data": {
            "email": email,
            "verify_code_required": True,
            "verify_code_length": VERIFICATION_CODE_LENGTH,
            "code_expires_in_minutes": VERIFICATION_CODE_TTL_MINUTES,
        },
    }
    if _check_login_email_cooldown(email):
        return jsonify(generic_response)

    _mark_login_email_sent(email)
    session = get_session()
    try:
        sub = (
            session.query(EmailSubscriber)
            .filter_by(email=email, verified=True, active=True)
            .first()
        )
        if not sub:
            return jsonify(generic_response)

        login_code = _issue_login_code(sub)
        # 独立登录请求不向前端暴露桥接票据；验证码或确认页本身即可完成登录。
        raw_challenge, _ = _create_auth_challenge(session, sub, "login")
        base_url = _get_public_base_url()
        login_url = f"{base_url}/api/login/{raw_challenge}"
        subscribe_url = f"{base_url}/subscribe"
        if not send_login_code_email(email, login_url, login_code, subscribe_url):
            session.rollback()
            logger.warning("登录邮件未发送: {}", mask_email(email))
            return jsonify(generic_response)

        commit_with_retry(session)
        _mark_login_email_sent(email)
        # 登录请求不把桥接票据暴露给前端；避免通过响应字段区分邮箱是否存在。
        return jsonify(generic_response)
    except Exception as exc:
        session.rollback()
        logger.exception("发送登录邮件失败")
        if is_database_locked_error(exc):
            return _database_busy_json("登录")
        return jsonify({"success": False, "error": "登录邮件发送失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/login/<token>")
def api_login(token):
    """展示一次性登录链接的确认页；真正消费由随后 POST 完成。"""
    return redirect(f"/verify/{token}?purpose=login")


@app.route("/api/login/<token>/confirm", methods=["POST"])
def api_login_confirm(token):
    """消费一次性登录链接并签发门户会话。"""
    session = get_session()
    try:
        sub, error = _consume_auth_challenge(session, token, "login")
        if error:
            return jsonify({
                "success": False,
                "error": "登录链接无效、已过期或已经使用，请回到订阅页重新发送登录邮件",
            }), 404
        commit_with_retry(session)
        resp = jsonify({
            "success": True,
            "message": "登录成功，正在进入课程门户。",
            "data": {
                "email": mask_email(sub.email),
                "portal_url": _portal_url_for_subscriber(sub),
            },
        })
        return _set_portal_session_cookie(resp, sub.token)
    except Exception as e:
        session.rollback()
        logger.exception("确认登录链接失败")
        if is_database_locked_error(e):
            logger.warning("登录链接确认遇到数据库锁竞争")
            return _database_busy_json("登录")
        return jsonify({"success": False, "error": "登录失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/verify/<token>")
def api_verify(token):
    """验证链接入口。旧的长期订阅 token 不再具有验证权限。"""
    return redirect(f"/verify/{token}")


@app.route("/api/verify/<token>/confirm", methods=["POST"])
def api_verify_confirm(token):
    session = get_session()
    try:
        sub, error = _consume_auth_challenge(session, token, "verify")
        if error:
            return jsonify({
                "success": False,
                "error": "验证链接无效、已过期或已经使用，请回到订阅页重新发送验证邮件",
            }), 404

        commit_with_retry(session)
        resp = jsonify({
            "success": True,
            "message": "邮箱验证成功",
            "data": {
                "email": mask_email(sub.email),
                "portal_url": _portal_url_for_subscriber(sub),
            },
        })
        return _set_portal_session_cookie(resp, sub.token)
    except Exception as e:
        session.rollback()
        logger.exception("verify confirm failed")
        if is_database_locked_error(e):
            logger.warning("邮箱验证确认遇到数据库锁竞争")
            return _database_busy_json("验证")
        return jsonify({"success": False, "error": "验证失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscribe/verify-code", methods=["POST"])
def api_subscribe_verify_code():
    data = request.get_json(silent=True) or {}
    email = _normalize_email(data.get("email"))
    code = _normalize_verification_code(data.get("code") or "")
    bridge_ticket = (data.get("bridge_ticket") or "").strip()

    if not email:
        return jsonify({"success": False, "error": "请输入订阅时使用的邮箱"}), 400
    if len(code) != VERIFICATION_CODE_LENGTH:
        return jsonify({"success": False, "error": f"请输入邮件中的 {VERIFICATION_CODE_LENGTH} 位验证码"}), 400

    session = get_session()
    try:
        sub, error = _verify_subscriber_code(session, email, code, bridge_ticket)
        if error in {"missing", "missing_code"}:
            return jsonify({"success": False, "error": "邮箱或验证码不正确，请回到订阅页重新获取验证码"}), 400
        if error == "expired_code":
            return jsonify({"success": False, "error": "验证码已过期，请回到订阅页重新发送验证邮件"}), 410
        if error == "too_many_attempts":
            commit_with_retry(session)
            return jsonify({"success": False, "error": "验证码尝试次数过多，请重新发送邮件"}), 429
        if error in {"invalid_code", "code_mismatch"}:
            commit_with_retry(session)
            return jsonify({"success": False, "error": f"验证码不正确，请检查邮件中的 {VERIFICATION_CODE_LENGTH} 位数字"}), 400

        commit_with_retry(session)
        resp = jsonify({
            "success": True,
            "message": "邮箱验证成功，正在进入课程门户。",
            "data": {
                "email": mask_email(sub.email),
                "portal_url": _portal_url_for_subscriber(sub),
            },
        })
        return _set_portal_session_cookie(resp, sub.token)
    except Exception as e:
        session.rollback()
        logger.exception("subscribe verify code failed")
        if is_database_locked_error(e):
            logger.warning("验证码验证遇到数据库锁竞争")
            return _database_busy_json("验证")
        return jsonify({"success": False, "error": "验证码验证失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscribe/bridge/<ticket>/status", methods=["GET"])
def api_subscribe_bridge_status(ticket):
    """Check cross-device bridge ticket status."""
    now = business_now()
    session = get_session()
    try:
        bridge = session.query(LoginBridgeTicket).filter_by(ticket=ticket).first()
        if not bridge:
            return jsonify({"success": False, "error": "找不到这次验证记录，请重新发送邮件"}), 404
        expired = bool(bridge.expires_at and bridge.expires_at <= now)
        subscriber = session.query(EmailSubscriber).filter_by(id=bridge.subscriber_id).first()
        return jsonify({
            "success": True,
            "data": {
                "verified": bool(bridge.verified and not expired),
                "expired": expired,
                "email": mask_email(subscriber.email if subscriber else ""),
                "expires_in": max(0, int((bridge.expires_at - now).total_seconds())) if bridge.expires_at else 0,
            },
        })
    finally:
        session.close()


@app.route("/api/subscribe/bridge/<ticket>/claim", methods=["POST"])
def api_subscribe_bridge_claim(ticket):
    """Claim bridge login on current device after verification."""
    now = business_now()
    session = get_session()
    try:
        bridge = session.query(LoginBridgeTicket).filter_by(ticket=ticket).first()
        if not bridge:
            return jsonify({"success": False, "error": "找不到这次验证记录，请重新发送邮件"}), 404
        if bridge.expires_at and bridge.expires_at <= now:
            return jsonify({"success": False, "error": "验证记录已过期，请重新发送邮件"}), 410
        if not bridge.verified:
            return jsonify({"success": False, "error": "邮箱验证还没有完成"}), 409
        if bridge.claimed_at:
            return jsonify({"success": False, "error": "这次验证已经领取过登录状态"}), 409

        sub = (
            session.query(EmailSubscriber)
            .filter_by(id=bridge.subscriber_id, verified=True, active=True)
            .first()
        )
        if not sub:
            return jsonify({"success": False, "error": "订阅状态不可用，请重新登录"}), 409

        bridge.claimed_at = now
        commit_with_retry(session)
        resp = jsonify({
            "success": True,
            "message": "登录已完成",
            "data": {
                "email": mask_email(sub.email),
                "portal_url": _portal_url_for_subscriber(sub),
            },
        })
        return _set_portal_session_cookie(resp, sub.token)
    except Exception as e:
        session.rollback()
        logger.exception("bridge login failed")
        if is_database_locked_error(e):
            logger.warning("桥接登录遇到数据库锁竞争")
            return _database_busy_json("登录")
        return jsonify({"success": False, "error": "登录失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/unsubscribe/<token>")
def api_unsubscribe(token):
    """退订"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token).first()
        if not sub:
            return redirect("/subscribe?result=invalid")
        sub.active = False
        commit_with_retry(session)
        resp = make_response(redirect("/subscribe?result=unsubscribed"))
        return _clear_portal_session_cookie(resp)
    except Exception as e:
        session.rollback()
        logger.exception("退订失败")
        return redirect("/subscribe?result=invalid")
    finally:
        session.close()


@app.route("/api/pause/<token>")
def api_pause_by_token(token):
    """邮件直达：暂停推送 24 小时"""
    try:
        hours = max(1, min(int(request.args.get("hours", 24)), 168))
    except (TypeError, ValueError):
        return redirect("/subscribe?result=invalid")
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(token=token).first()
        if not sub:
            return redirect("/subscribe?result=invalid")
        sub.push_paused_until = business_now() + timedelta(hours=hours)
        commit_with_retry(session)
        return redirect(f"/subscribe?result=paused&hours={hours}")
    except Exception as e:
        session.rollback()
        logger.exception("邮件暂停推送失败")
        return redirect("/subscribe?result=invalid")
    finally:
        session.close()


@app.route("/api/unsubscribe", methods=["POST"])
def api_unsubscribe_session():
    """按会话退订"""
    token = _get_session_token()
    if not token:
        return jsonify({"success": False, "error": "未登录"}), 401

    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401
        sub.active = False
        commit_with_retry(session)
        resp = jsonify({"success": True, "message": "已退订"})
        return _clear_portal_session_cookie(resp)
    except Exception as e:
        session.rollback()
        logger.exception("按会话退订失败")
        return jsonify({"success": False, "error": "退订失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscribers")
def api_subscribers():
    """管理端：查看所有订阅者（聚合推送/提醒/活跃度数据）"""
    now = business_now()
    cutoff_7d = now - timedelta(days=7)
    dormant_cutoff = now - timedelta(days=14)
    session = get_session()
    try:
        subs = session.query(EmailSubscriber).order_by(EmailSubscriber.created_at.desc()).all()
        sub_ids = [s.id for s in subs]
        deliveries_7d_map = {}
        last_delivery_map = {}
        pending_reminders_map = {}

        if sub_ids:
            delivery_rows = (
                session.query(
                    NotificationEvent.subscriber_id,
                    func.count(NotificationEvent.id),
                )
                .filter(
                    NotificationEvent.subscriber_id.in_(sub_ids),
                    NotificationEvent.sent_at >= cutoff_7d,
                    NotificationEvent.success == True,  # noqa: E712
                )
                .group_by(NotificationEvent.subscriber_id)
                .all()
            )
            deliveries_7d_map = {subscriber_id: count for subscriber_id, count in delivery_rows}

            last_delivery_rows = (
                session.query(
                    NotificationEvent.subscriber_id,
                    func.max(NotificationEvent.sent_at),
                )
                .filter(
                    NotificationEvent.subscriber_id.in_(sub_ids),
                    NotificationEvent.success == True,  # noqa: E712
                )
                .group_by(NotificationEvent.subscriber_id)
                .all()
            )
            last_delivery_map = {subscriber_id: sent_at for subscriber_id, sent_at in last_delivery_rows}

            pending_reminder_rows = (
                session.query(
                    CourseReminder.subscriber_id,
                    func.count(CourseReminder.id),
                )
                .filter(
                    CourseReminder.subscriber_id.in_(sub_ids),
                    CourseReminder.sent == False,  # noqa: E712
                )
                .group_by(CourseReminder.subscriber_id)
                .all()
            )
            pending_reminders_map = {subscriber_id: count for subscriber_id, count in pending_reminder_rows}

        result = []
        summary = {
            "total": len(subs),
            "verified": 0,
            "unverified": 0,
            "active_sending": 0,
            "paused": 0,
            "inactive": 0,
            "dormant": 0,
            "joined_7d": 0,
        }
        for s in subs:
            d = s.to_dict()
            paused = bool(s.push_paused_until and now < s.push_paused_until)
            d["push_is_paused"] = paused
            d["push_paused_until"] = s.push_paused_until.strftime("%Y-%m-%d %H:%M") if paused else None
            d["account_status"] = "active" if s.active else "inactive"
            d["verification_status"] = "verified" if s.verified else "unverified"
            d["deliveries_7d"] = deliveries_7d_map.get(s.id, 0)
            last_delivered_at = last_delivery_map.get(s.id)
            d["last_delivered_at"] = last_delivered_at.strftime("%Y-%m-%d %H:%M") if last_delivered_at else None
            d["pending_reminders"] = pending_reminders_map.get(s.id, 0)
            d["last_portal_seen_at"] = s.last_portal_seen_at.strftime("%Y-%m-%d %H:%M") if s.last_portal_seen_at else None
            d["is_dormant"] = bool(s.active and s.verified and (not s.last_portal_seen_at or s.last_portal_seen_at < dormant_cutoff))
            d["joined_recently"] = bool(s.created_at and s.created_at >= cutoff_7d)
            result.append(d)

            if s.verified:
                summary["verified"] += 1
            else:
                summary["unverified"] += 1
            if not s.active:
                summary["inactive"] += 1
            elif paused:
                summary["paused"] += 1
            else:
                summary["active_sending"] += 1
            if d["is_dormant"]:
                summary["dormant"] += 1
            if d["joined_recently"]:
                summary["joined_7d"] += 1

        return jsonify({"success": True, "data": result, "total": len(result), "summary": summary})
    finally:
        session.close()


@app.route("/api/remind/<token>/<course_id>", methods=["GET", "POST"])
def api_remind(token, course_id):
    """注册选课提醒：用户点击邮件中的「提醒我选课」按钮"""
    is_json = request.is_json or request.headers.get('Accept', '').startswith('application/json')
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(
            token=token, verified=True, active=True
        ).first()
        if not sub:
            if is_json:
                return jsonify({"success": False, "error": "邮件操作链接无效或已过期"}), 404
            return redirect("/subscribe?result=invalid")

        course = session.query(Course).filter_by(id=course_id).first()
        if not course:
            if is_json:
                return jsonify({"success": False, "error": "课程不存在"}), 404
            return redirect("/subscribe?result=invalid")
        if is_course_expired(course) or course.remaining <= 0:
            if is_json:
                return jsonify({"success": False, "error": "该课程当前不可提醒（已满或已过期）"}), 400
            return redirect("/subscribe?result=invalid")

        # 防止重复注册
        existing = (
            session.query(CourseReminder)
            .filter_by(subscriber_id=sub.id, course_id=course_id, sent=False)
            .first()
        )
        if not existing:
            reminder = CourseReminder(
                subscriber_id=sub.id,
                course_id=course_id,
                remind_before_minutes=5,
            )
            session.add(reminder)
            commit_with_retry(session)
            logger.info(f"选课提醒已注册: {mask_email(sub.email)} -> {course.name}")

        if is_json:
            return jsonify({"success": True, "message": f"已注册提醒: {course.name}"})
        return redirect("/subscribe?result=reminded")
    except Exception as e:
        session.rollback()
        logger.exception("注册选课提醒失败")
        if is_database_locked_error(e):
            logger.warning("注册选课提醒遇到数据库锁竞争")
            if is_json:
                return _database_busy_json("提醒")
            return redirect("/subscribe?result=invalid")
        if is_json:
            return jsonify({"success": False, "error": "提醒注册失败，请稍后重试"}), 500
        return redirect("/subscribe?result=invalid")
    finally:
        session.close()


# ========== 测试工具 ==========

@app.route("/api/test-email", methods=["POST"])
def api_test_email():
    """发送测试邮件：用数据库中真实课程数据构建邮件并发送到指定邮箱"""
    from src.push.email_push import _build_notification_html, _send_raw_email

    data = request.get_json(silent=True) or {}
    to_email = _normalize_email(data.get("email"))
    if not to_email:
        return jsonify({"success": False, "error": "请提供有效的目标邮箱"}), 400

    session = get_session()
    try:
        now = business_now()
        candidates = session.query(Course).filter(Course.expired == False).limit(20).all()  # noqa: E712
        courses = [course for course in candidates if not is_course_expired(course, now)][:4]
        if not courses:
            return jsonify({"success": False, "error": "数据库中没有可用课程"}), 404

        base_url = _get_public_base_url()
        html = _build_notification_html(
            courses,
            unsubscribe_url=f"{base_url}/api/unsubscribe/test",
            sub_token="test",
            base_url=base_url,
        )
        ok = _send_raw_email(to_email, f"[测试] 博雅课程通知 ({len(courses)} 门)", html)
        if ok:
            logger.info(f"测试邮件发送成功 -> {mask_email(to_email)}")
            return jsonify({"success": True, "message": f"测试邮件已发送到 {to_email}，共 {len(courses)} 门课程"})
        else:
            return jsonify({"success": False, "error": "邮件发送失败，请检查 SMTP 配置"}), 500
    except Exception:
        logger.exception("测试邮件失败")
        return jsonify({"success": False, "error": "测试邮件发送失败，请稍后重试"}), 500
    finally:
        session.close()


# ========== 用户门户 ==========

@app.route("/portal")
def portal_page():
    """用户门户页面；身份只由 HttpOnly Cookie 确定。"""
    if request.args.get("token"):
        return redirect("/subscribe?result=login_required")
    return render_template("portal.html")


@app.route("/api/subscriber/lookup", methods=["POST"])
def api_subscriber_lookup():
    """返回当前会话的订阅者信息，不接受邮箱作为身份凭据。"""
    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "未登录或会话已失效"}), 401

        return jsonify({
            "success": True,
            "data": {
                **sub.to_dict(),
            },
        })
    finally:
        session.close()


@app.route("/api/remind/<course_id>", methods=["POST"])
def api_remind_session(course_id):
    """按会话注册选课提醒"""
    token = _get_session_token()
    if not token:
        return jsonify({"success": False, "error": "未登录"}), 401
    return api_remind(token, course_id)


@app.route("/api/subscriber/session", methods=["GET"])
def api_subscriber_session():
    """从会话 Cookie 获取当前订阅者"""
    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401
        is_first_portal_visit = sub.last_portal_seen_at is None and sub.onboarding_seen_at is None
        now = business_now()
        should_persist_last_seen = (
            sub.last_portal_seen_at is None
            or (now - sub.last_portal_seen_at) >= timedelta(minutes=10)
        )
        if should_persist_last_seen:
            sub.last_portal_seen_at = now
            try:
                commit_with_retry(session)
            except Exception as e:
                logger.warning("skip last_portal_seen_at update due to DB contention")
                session.rollback()
        resp = jsonify({
            "success": True,
            "data": {
                **sub.to_dict(),
                "show_onboarding": is_first_portal_visit,
            },
        })
        return resp
    except Exception:
        logger.exception("获取门户会话失败")
        return jsonify({"success": False, "error": "加载门户会话失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscriber/session/onboarding-seen", methods=["POST"])
def api_subscriber_onboarding_seen():
    """标记当前订阅者已看过首次引导"""
    token = _get_session_token()
    if not token:
        return jsonify({"success": False, "error": "未登录"}), 401

    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401
        if not sub.onboarding_seen_at:
            sub.onboarding_seen_at = business_now()
            commit_with_retry(session)
        return jsonify({"success": True, "message": "首次引导已记录"})
    except Exception as e:
        session.rollback()
        logger.exception("mark onboarding seen failed")
        if is_database_locked_error(e):
            logger.warning("标记首次引导已读遇到数据库锁竞争")
            return _database_busy_json("保存设置")
        return jsonify({"success": False, "error": "引导状态保存失败"}), 500
    finally:
        session.close()


@app.route("/api/session/clear", methods=["POST"])
def api_session_clear():
    """清除门户会话"""
    resp = jsonify({"success": True, "message": "会话已清除"})
    return _clear_portal_session_cookie(resp)


@app.route("/api/subscriber/session", methods=["PUT"])
@app.route("/api/subscriber/<token>", methods=["PUT"])
def api_subscriber_update(token=None):
    """更新订阅者偏好设置"""
    if token:
        return jsonify({"success": False, "error": "请使用会话接口更新设置"}), 410
    if not _get_session_token():
        return jsonify({"success": False, "error": "未登录"}), 401
    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401

        data = request.get_json(silent=True) or {}
        if "categories" in data:
            sub.categories = data["categories"]
        if "campus_filter" in data:
            sub.campus_filter = data["campus_filter"]
        if "self_sign_only" in data:
            sub.self_sign_only = bool(data["self_sign_only"])
        if "active" in data:
            sub.active = bool(data["active"])

        commit_with_retry(session)
        logger.info(f"订阅者偏好已更新: {mask_email(sub.email)}")
        return jsonify({"success": True, "message": "偏好已保存", "data": sub.to_dict()})
    except Exception as e:
        session.rollback()
        logger.exception("更新订阅者偏好失败")
        if is_database_locked_error(e):
            logger.warning("更新订阅偏好遇到数据库锁竞争")
            return _database_busy_json("保存设置")
        return jsonify({"success": False, "error": "偏好保存失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscriber/session/reminders")
@app.route("/api/subscriber/<token>/reminders")
def api_subscriber_reminders(token=None):
    """获取订阅者的选课提醒列表（含课程详情）"""
    if token:
        return jsonify({"success": False, "error": "请使用会话接口查看提醒"}), 410
    if not _get_session_token():
        return jsonify({"success": False, "error": "未登录"}), 401
    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401

        result = _serialize_course_reminders(session, sub.id)
        return jsonify({"success": True, "data": result, "total": len(result)})
    except Exception:
        logger.exception("获取提醒列表失败")
        return jsonify({"success": False, "error": "获取提醒列表失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscriber/session/notifications")
@app.route("/api/subscriber/<token>/notifications")
def api_subscriber_notifications(token=None):
    """获取订阅者通知中心时间线（默认最近 24 小时）"""
    if token:
        return jsonify({"success": False, "error": "请使用会话接口查看通知"}), 410
    if not _get_session_token():
        return jsonify({"success": False, "error": "未登录"}), 401

    hours_raw = request.args.get("hours", "24")
    limit_raw = request.args.get("limit", "100")

    try:
        hours = max(1, min(int(hours_raw), 168))
    except ValueError:
        hours = 24
    try:
        limit = max(1, min(int(limit_raw), 300))
    except ValueError:
        limit = 100

    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401

        cutoff = business_now() - timedelta(hours=hours)
        events = (
            session.query(NotificationEvent)
            .filter(NotificationEvent.subscriber_id == sub.id)
            .filter(NotificationEvent.sent_at >= cutoff)
            .order_by(NotificationEvent.sent_at.desc())
            .limit(limit)
            .all()
        )

        def _extract_delivery_mode(event):
            # 优先使用专用列，回退到 message 字段兼容旧记录
            dm = getattr(event, "delivery_mode", "") or ""
            if not dm and event.message:
                for part in event.message.split(";"):
                    if part.startswith("delivery_mode="):
                        dm = part.split("=", 1)[1].strip()
                        break
            return dm

        data = [{
            "id": e.id,
            "course_id": e.course_id,
            "course_name": e.course_name,
            "course_category": e.course_category,
            "event_type": e.event_type,
            "delivery_mode": _extract_delivery_mode(e),
            "channel": e.channel,
            "success": e.success,
            "sent_at": e.sent_at.strftime("%Y-%m-%d %H:%M") if e.sent_at else "",
        } for e in events]

        return jsonify({"success": True, "data": data, "total": len(data)})
    finally:
        session.close()


# ========== 管理工具 ==========

@app.route("/api/admin/broadcast/service-update", methods=["POST"])
def api_admin_broadcast_service_update():
    """管理端：向现有已验证用户发送站点入口调整通知。"""
    from src.push.email_push import send_service_update_to_subscribers

    session = get_session()
    try:
        subscribers = (
            session.query(EmailSubscriber)
            .filter_by(active=True, verified=True)
            .order_by(EmailSubscriber.created_at.asc())
            .all()
        )
        if not subscribers:
            return jsonify({"success": False, "error": "没有可发送的已验证用户"}), 404

        result = send_service_update_to_subscribers(subscribers, _get_public_base_url())
        logger.info(
            "管理端发送站点调整通知: total={} success={} failed={}",
            result["total"],
            result["success"],
            result["failed"],
        )
        return jsonify({
            "success": True,
            "message": f"通知发送完成：成功 {result['success']}，失败 {result['failed']}",
            "data": result,
        })
    except Exception:
        logger.exception("发送站点调整通知失败")
        return jsonify({"success": False, "error": "站点通知发送失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/manual-push", methods=["POST"])
def api_manual_push():
    """手动推送指定课程给所有活跃邮件订阅者"""
    from src.push.email_push import send_email_notification

    data = request.get_json(silent=True) or {}
    course_id = data.get("course_id")
    if not course_id:
        return jsonify({"success": False, "error": "缺少 course_id"}), 400

    session = get_session()
    try:
        course = session.query(Course).filter_by(id=course_id).first()
        if not course:
            return jsonify({"success": False, "error": "课程不存在"}), 404

        # 发送邮件推送
        try:
            loop = asyncio.new_event_loop()
            try:
                sent_count = loop.run_until_complete(send_email_notification([course]))
            finally:
                loop.close()
        except Exception:
            logger.exception("手动推送执行失败")
            return jsonify({"success": False, "error": "推送失败，请稍后重试"}), 500

        if sent_count > 0:
            return jsonify({"success": True, "message": f"已发送: {course.name} ({sent_count} 个订阅者)"})
        else:
            return jsonify({"success": False, "error": "邮件发送失败"}), 500
    finally:
        session.close()


# ========== 管理端：用户管理 ==========

@app.route("/api/admin/subscriber/<int:sub_id>/toggle-active", methods=["POST"])
def api_admin_toggle_subscriber(sub_id):
    """管理端：切换订阅者激活状态"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(id=sub_id).first()
        if not sub:
            return jsonify({"success": False, "error": "用户不存在"}), 404
        sub.active = not sub.active
        session.commit()
        status = "已激活" if sub.active else "已停用"
        logger.info(f"管理端切换用户状态: {mask_email(sub.email)} -> {status}")
        return jsonify({"success": True, "active": sub.active, "message": f"{sub.email} {status}"})
    except Exception:
        session.rollback()
        logger.exception("管理端切换用户状态失败")
        return jsonify({"success": False, "error": "用户状态更新失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/admin/subscriber/<int:sub_id>/clear-pause", methods=["POST"])
def api_admin_clear_pause(sub_id):
    """管理端：解除推送暂停"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(id=sub_id).first()
        if not sub:
            return jsonify({"success": False, "error": "用户不存在"}), 404
        sub.push_paused_until = None
        session.commit()
        logger.info(f"管理端解除推送暂停: {mask_email(sub.email)}")
        return jsonify({"success": True, "message": f"{sub.email} 推送暂停已解除"})
    except Exception:
        session.rollback()
        logger.exception("管理端解除推送暂停失败")
        return jsonify({"success": False, "error": "解除暂停失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/cleanup-expired", methods=["POST"])
def api_cleanup_expired():
    """清理 30 天以上的过期课程"""
    days = request.get_json(silent=True) or {}
    try:
        max_days = max(1, min(int(days.get("days", 30)), 3650))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "清理天数无效"}), 400

    session = get_session()
    try:
        cutoff = business_now() - timedelta(days=max_days)
        old_courses = (
            session.query(Course)
            .filter(Course.expired == True)  # noqa: E712
            .filter(Course.enroll_end < cutoff)
            .all()
        )

        count = len(old_courses)
        for c in old_courses:
            session.delete(c)
        session.commit()

        logger.info(f"清理了 {count} 门过期超过 {max_days} 天的课程")
        return jsonify({"success": True, "deleted": count, "message": f"已清理 {count} 门课程"})
    except Exception:
        session.rollback()
        logger.exception("清理过期课程失败")
        return jsonify({"success": False, "error": "清理过期课程失败，请稍后重试"}), 500
    finally:
        session.close()


# ========== 用户推送控制 ==========

@app.route("/api/subscriber/session/pause-push", methods=["POST"])
def api_pause_push():
    """暂停推送 N 小时（默认 24 小时）"""
    token = _get_session_token()
    if not token:
        return jsonify({"success": False, "error": "未登录"}), 401

    data = request.get_json(silent=True) or {}
    try:
        hours = max(1, min(int(data.get("hours", 24)), 168))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "暂停时长无效，请输入 1 到 168 小时"}), 400

    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401
        sub.push_paused_until = business_now() + timedelta(hours=hours)
        commit_with_retry(session)
        until_str = sub.push_paused_until.strftime("%Y-%m-%d %H:%M")
        logger.info(f"推送已暂停 {hours} 小时: {mask_email(sub.email)} (至 {until_str})")
        return jsonify({"success": True, "message": f"推送已暂停 {hours} 小时，至 {until_str}", "paused_until": until_str})
    except Exception as e:
        session.rollback()
        if is_database_locked_error(e):
            logger.warning("按会话暂停推送遇到数据库锁竞争")
            return _database_busy_json("保存设置")
        logger.exception("按会话暂停推送失败")
        return jsonify({"success": False, "error": "暂停推送失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/subscriber/session/resume-push", methods=["POST"])
def api_resume_push():
    """恢复推送（取消暂停）"""
    token = _get_session_token()
    if not token:
        return jsonify({"success": False, "error": "未登录"}), 401

    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401
        sub.push_paused_until = None
        commit_with_retry(session)
        logger.info(f"推送已恢复: {mask_email(sub.email)}")
        return jsonify({"success": True, "message": "推送已恢复"})
    except Exception as e:
        session.rollback()
        if is_database_locked_error(e):
            logger.warning("按会话恢复推送遇到数据库锁竞争")
            return _database_busy_json("保存设置")
        logger.exception("按会话恢复推送失败")
        return jsonify({"success": False, "error": "恢复推送失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/portal/highlights")
def api_portal_highlights():
    """门户首页亮点数据：近期开抢倒计时、今日新发现、待提醒数量"""
    token = _get_session_token()
    if not token:
        return jsonify({"success": False, "error": "未登录"}), 401

    now = business_now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    session = get_session()
    try:
        sub = _current_subscriber(session)
        if not sub:
            return jsonify({"success": False, "error": "会话失效"}), 401

        # 近期开抢：选课开始时间在未来 24 小时内，且有名额
        soon_cutoff = now + timedelta(hours=24)
        upcoming_candidates = (
            session.query(Course)
            .filter(Course.expired.is_(False))
            .filter(Course.enroll_start.isnot(None))
            .filter(Course.enroll_start > now)
            .filter(Course.enroll_start <= soon_cutoff)
            .filter((Course.capacity - Course.enrolled) > 0)
            .filter(or_(Course.end_time.is_(None), Course.end_time > now))
            .filter(or_(Course.enroll_end.is_(None), Course.enroll_end > now))
            .order_by(Course.enroll_start)
            .limit(20)
            .all()
        )
        upcoming = [
            course for course in upcoming_candidates
            if not is_course_expired(course, now)
        ][:5]
        upcoming_data = [{
            "id": c.id,
            "name": c.name,
            "campus": c.campus,
            "category": c.category,
            "remaining": c.remaining,
            "enroll_start": c.enroll_start.strftime("%Y-%m-%d %H:%M"),
            "seconds_left": max(0, int((c.enroll_start - now).total_seconds())),
        } for c in upcoming]

        # 今日新发现的课程数（first_seen 在今天）
        today_new = session.query(func.count(Course.id)).filter(
            Course.first_seen >= today_start,
            Course.expired.is_(False),
            or_(Course.end_time.is_(None), Course.end_time > now),
            or_(Course.enroll_end.is_(None), Course.enroll_end > now),
        ).scalar() or 0
        today_new = int(today_new)

        # 用户待发提醒数（此订阅者未发送的提醒）
        pending_reminder_items = _serialize_course_reminders(session, sub.id, pending_only=True)
        pending_reminders = len(pending_reminder_items)

        # 推送暂停状态
        push_paused_until = None
        if sub.push_paused_until and now < sub.push_paused_until:
            push_paused_until = sub.push_paused_until.strftime("%Y-%m-%d %H:%M")

        return jsonify({
            "success": True,
            "data": {
                "upcoming_courses": upcoming_data,
                "upcoming_count": len(upcoming_data),
                "today_new_count": today_new,
                "pending_reminders": pending_reminders,
                "pending_reminder_items": pending_reminder_items,
                "push_paused_until": push_paused_until,
            },
        })
    except Exception:
        logger.exception("获取门户亮点失败")
        return jsonify({"success": False, "error": "获取门户亮点失败，请稍后重试"}), 500
    finally:
        session.close()


@app.route("/api/admin/subscriber/<int:sub_id>/pause-push", methods=["POST"])
def api_admin_pause_push(sub_id):
    """管理端：暂停订阅者推送 N 小时"""
    session = get_session()
    try:
        sub = session.query(EmailSubscriber).filter_by(id=sub_id).first()
        if not sub:
            return jsonify({"success": False, "error": "用户不存在"}), 404

        data = request.get_json(silent=True) or {}
        try:
            hours = max(1, min(int(data.get("hours", 24)), 24 * 30))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "暂停时长无效"}), 400

        sub.push_paused_until = business_now() + timedelta(hours=hours)
        session.commit()
        until_str = sub.push_paused_until.strftime("%Y-%m-%d %H:%M")
        logger.info(f"管理端暂停推送: {mask_email(sub.email)} -> {until_str}")
        return jsonify({
            "success": True,
            "message": f"{sub.email} 已暂停推送 {hours} 小时（至 {until_str}）",
            "paused_until": until_str,
        })
    except Exception:
        session.rollback()
        logger.exception("管理员暂停推送失败")
        return jsonify({"success": False, "error": "暂停推送失败，请稍后重试"}), 500
    finally:
        session.close()

