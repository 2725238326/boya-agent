"""Web 层的共用安全边界。

这里集中处理管理接口认证、同源请求检查和安全响应头，避免每个路由
各自复制一套容易遗漏的判断。
"""

from __future__ import annotations

import hmac
import os
from functools import wraps
from urllib.parse import urlsplit

from flask import jsonify, request


ADMIN_EXACT_PATHS = {
    "/api/config",
    "/api/enroll/toggle",
    "/api/trigger",
    "/api/manual-push",
    "/api/test-email",
    "/api/cleanup-expired",
    "/api/subscribers",
    "/api/status",
}


def mask_email(email: str) -> str:
    """返回适合日志和非私有界面的邮箱脱敏值。"""
    normalized = (email or "").strip().lower()
    if "@" not in normalized:
        return "匿名"
    local, domain = normalized.split("@", 1)
    if len(local) <= 2:
        masked = f"{local[:1]}*"
    else:
        masked = f"{local[:2]}{'*' * max(1, len(local) - 2)}"
    return f"{masked}@{domain}"


def requires_admin(path: str) -> bool:
    """判断请求是否属于管理页面或管理接口。"""
    return (
        path.startswith("/admin")
        or path.startswith("/api/admin/")
        or path.startswith("/api/logs/")
        or path in ADMIN_EXACT_PATHS
    )


def _configured_admin_credentials() -> tuple[str, str, str]:
    def configured(name: str) -> str:
        value = (os.getenv(name) or "").strip()
        return "" if value.lower().startswith("replace-with-") else value

    return (
        configured("ADMIN_USERNAME"),
        configured("ADMIN_PASSWORD"),
        configured("ADMIN_API_TOKEN"),
    )


def admin_credentials_configured() -> bool:
    username, password, api_token = _configured_admin_credentials()
    return bool(api_token or (username and password))


def is_admin_authorized() -> bool:
    """验证 Bearer 管理令牌或 Basic Auth 管理账号。"""
    username, password, api_token = _configured_admin_credentials()
    authorization = request.headers.get("Authorization", "")

    if api_token and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
        return hmac.compare_digest(supplied, api_token)

    if not username or not password:
        return False

    # Flask 已经负责解析 Basic Auth；额外使用 compare_digest 避免普通字符串
    # 比较把密码直接带入可观察的比较路径。
    credentials = request.authorization
    if not credentials:
        return False
    return hmac.compare_digest(credentials.username or "", username) and hmac.compare_digest(
        credentials.password or "", password
    )


def admin_error_response():
    if not admin_credentials_configured():
        return jsonify({
            "success": False,
            "code": "admin_auth_not_configured",
            "error": "管理员认证尚未配置",
        }), 503

    response = jsonify({
        "success": False,
        "code": "admin_auth_required",
        "error": "需要管理员权限",
    })
    response.headers["WWW-Authenticate"] = 'Basic realm="BOYA Admin"'
    return response, 401


def admin_required(view):
    """为独立蓝图或少量特殊路由提供可复用的管理员装饰器。"""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin_authorized():
            return admin_error_response()
        return view(*args, **kwargs)

    return wrapped


def _allowed_origins() -> set[str]:
    configured = (os.getenv("APP_ALLOWED_ORIGINS") or "").strip()
    origins = {item.rstrip("/") for item in configured.split(",") if item.strip()}

    public_base = (os.getenv("APP_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if public_base:
        origins.add(public_base)
    return origins


def is_same_origin_request() -> bool:
    """校验带 Origin/Referer 的状态修改请求是否来自允许来源。

    没有这两个请求头的脚本客户端仍可调用接口；门户 Cookie 使用 SameSite=Lax，
    部署文档同时要求通过 HTTPS 访问。这样不会破坏现有 CLI/任务调用，又能阻断
    浏览器跨站脚本带 Cookie 修改状态的常见路径。
    """
    header_value = request.headers.get("Origin") or request.headers.get("Referer")
    if not header_value:
        return True

    parsed = urlsplit(header_value)
    if not parsed.scheme or not parsed.netloc:
        return False
    origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    request_origin = request.host_url.rstrip("/")
    allowed = _allowed_origins()
    return origin == request_origin or origin in allowed


def security_headers(response):
    """添加与当前无内嵌第三方资源假设相符的基础安全响应头。"""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.path.startswith("/static/") and response.status_code == 200:
        response.headers.setdefault(
            "Cache-Control",
            "public, max-age=604800, immutable",
        )
    if request.is_secure or (request.headers.get("X-Forwarded-Proto") or "").lower() == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response
