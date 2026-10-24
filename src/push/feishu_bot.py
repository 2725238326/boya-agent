"""Signed Feishu group webhooks, durable jobs and independent rollout policy.

No webhook, signing secret, email, auth challenge or upstream exception text is
stored in a notification payload or returned by the administrator diagnostics.
"""

import asyncio
import base64
from datetime import timedelta
import hashlib
import hmac
import json
import os
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit, quote
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request

from loguru import logger

from src.course_state import get_check_in_display_label, is_course_expired
from src.notification_jobs import NotificationDeliveryResult, drain_notification_jobs, enqueue_notification_job
from src.runtime import is_staging
from src.time_utils import now as business_now

_transport_lock = threading.Lock()
_last_request_at = 0.0
_MAX_BODY_BYTES = 19000
_KINDS = {"courses": {"course_push", "daily_summary"}}


def _credential_config():
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    secret = os.getenv("FEISHU_WEBHOOK_SECRET", "").strip()
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != "open.feishu.cn"
            or not re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9-]+", parsed.path)
            or parsed.query or parsed.fragment or not secret):
        raise ValueError("Feishu webhook/signing configuration invalid")
    return url, secret


def enabled(kind: str) -> bool:
    if kind not in _KINDS or is_staging():
        return False
    if os.getenv(f"FEISHU_{kind.upper()}_ENABLED", "").strip().lower() != "true":
        return False
    try:
        _credential_config()
        return True
    except ValueError:
        return False


def readiness() -> dict:
    try:
        _credential_config()
        configured = True
    except ValueError:
        configured = False
    return {"configured": configured, "alerts_enabled": False, "role": "student_courses",
            "courses_enabled": enabled("courses"), "staging_blocked": is_staging()}


def signature(secret: str, timestamp: str) -> str:
    # Feishu uses timestamp + newline + secret as the HMAC key, empty message.
    key = f"{timestamp}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _send_sync(text: str, kind: str, *, probe=False, card=None) -> bool:
    """One attempt; outbox owns retries. Never follow a secret-bearing redirect."""
    global _last_request_at
    if is_staging() or (not probe and not enabled(kind)):
        return False
    try:
        url, secret = _credential_config()
        with _transport_lock:
            delay = max(0.0, 1.1 - (time.monotonic() - _last_request_at))
            if delay:
                time.sleep(delay)
            # Recheck policy after waiting; no disabled channel sends a backlog.
            if is_staging() or (not probe and not enabled(kind)):
                return False
            timestamp = str(int(time.time()))
            message = {"msg_type": "interactive", "card": card} if card is not None else (
                {"msg_type": "text", "content": {"text": text}})
            body = json.dumps({"timestamp": timestamp, "sign": signature(secret, timestamp), **message},
                              ensure_ascii=False).encode("utf-8")
            if len(body) > _MAX_BODY_BYTES:
                logger.warning("Feishu message exceeds configured size bound")
                return False
            request = Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
            # Avoid inheriting workstation proxy credentials; deployment uses
            # a direct verified HTTPS connection to the fixed Feishu hostname.
            opener = build_opener(ProxyHandler({}), _NoRedirect())
            _last_request_at = time.monotonic()
            with opener.open(request, timeout=10) as response:
                if response.status != 200:
                    return False
                raw = response.read(65537)
                if len(raw) > 65536:
                    return False
                payload = json.loads(raw)
            code = payload.get("code") if isinstance(payload, dict) else None
            if type(code) is not int or code != 0:
                logger.warning("Feishu rejected message: code={}", code if type(code) is int else "invalid")
                return False
            return True
    except HTTPError as error:
        logger.warning("Feishu HTTP failure: status={}", error.code)
        error.close()
    except Exception as error:
        # Exception strings may include the complete webhook URL or response.
        logger.warning("Feishu delivery failure: type={}", type(error).__name__)
    return False


async def send_text(text: str, kind: str) -> bool:
    return await asyncio.to_thread(_send_sync, text, kind)


async def send_card(card: dict, kind: str) -> bool:
    return await asyncio.to_thread(_send_sync, "", kind, card=card)


def _plain(value, limit=180) -> dict:
    # All upstream values remain plain_text, so names cannot inject links,
    # @mentions or lark_md markup into group notifications.
    return {"tag": "plain_text", "content": str(value or "待确认")[:limit]}


def _card(title: str, color: str, elements: list) -> dict:
    return {"config": {"wide_screen_mode": True},
            "header": {"template": color, "title": _plain(title)}, "elements": elements}


def _course_card(course, event_type: str, delivery_mode: str) -> dict:
    now = business_now()
    preview = bool(course.enroll_start and course.enroll_start > now)
    window_known = bool(course.enroll_start and course.enroll_end)
    title = "退选补位" if event_type == "snipe" else ("开选预告" if preview else "课程可报名")
    if not window_known:
        title = "课程信息 · 报名时间待确认"
    if delivery_mode == "digest_daily":
        title = "今日课程"
    reason = "此前已满，本轮采集发现重新有名额。" if event_type == "snipe" else (
        "尚未开选，请留意下面的开选时间。" if preview else "符合本群筛选，发出时仍在报名窗口内。")
    if not window_known:
        reason = "报名时间未完整取得，请先到官方系统核对。"
    def when(value):
        if not value:
            return "待确认"
        prefix = "%m-%d" if value.year == now.year else "%Y-%m-%d"
        return value.strftime(prefix) + f"（周{'一二三四五六日'[value.weekday()]}）" + value.strftime("%H:%M")
    def period(start, end):
        return f"{when(start)} — {end:%H:%M}" if start and end and start.date() == end.date() else f"{when(start)} — {when(end)}"
    places = f"剩余 {course.remaining} 个名额 / 容量 {course.capacity}" if course.capacity and course.capacity > 0 else "名额待确认"
    elements = [
        {"tag": "div", "text": _plain(reason)},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**{places}**"}},
        {"tag": "div", "text": _plain(f"{get_check_in_display_label(course)} · {course.campus or '校区待确认'}\n{course.category or '类别待确认'}")},
        {"tag": "hr"},
        {"tag": "div", "text": _plain(f"{'开选时间' if preview else '报名截止'}：{when(course.enroll_start if preview else course.enroll_end)}", 240)},
        {"tag": "div", "text": _plain(f"上课：{period(course.start_time, course.end_time)}\n地点：{course.location or '待确认'}", 300)},
        {"tag": "action", "actions": [
            {"tag": "button", "type": "primary", "text": _plain("前往官方选课"), "url": "https://bykc.buaa.edu.cn/"},
            {"tag": "button", "type": "default", "text": _plain("查看这门课"),
             "url": "https://buaaboya.top/?course=" + quote(str(course.id), safe="") + "#courses"}]},
        {"tag": "note", "elements": [_plain(f"采集于 {when(course.last_seen)}（北京时间）。名额会变化，以官方系统为准。", 240)]},
    ]
    restrictions = [str(getattr(course, key, "") or "") for key in ("open_college", "open_grade", "open_group")]
    restrictions = [value for value in restrictions if value and "全部" not in value and "不限" not in value]
    if restrictions:
        elements.insert(-2, {"tag": "div", "text": _plain("开放范围：" + "｜".join(restrictions), 300)})
    return _card(f"博雅 · {title}｜{course.name}", "orange" if event_type == "snipe" else "blue", elements)


def enqueue_courses(session, courses, *, event_type="new", delivery_mode="instant", summary_date=""):
    if not enabled("courses"):
        return []
    jobs = []
    for course in courses:
        if not course.last_seen or business_now() - course.last_seen > timedelta(minutes=10):
            continue
        daily = delivery_mode == "digest_daily"
        # One course per task avoids duplicate successful recipients when one
        # part of a batch fails. Recipient is a hash of the configured group URL.
        url, _ = _credential_config()
        target = hashlib.sha256(url.encode()).hexdigest()[:16]
        material = f"day:{summary_date}" if daily else (
            ("preview" if course.enroll_start and course.enroll_start > business_now() else "open") if event_type == "new"
            else f"reopen:{course.last_seen.isoformat()}"
        )
        # Preserve dedupe for messages sent by the previous production version.
        if event_type == "new" and not daily:
            from src.models import NotificationJob
            legacy_hash = hashlib.sha256(f"{target}:{course.id}:new:new".encode()).hexdigest()
            legacy = session.query(NotificationJob).filter_by(idempotency_key=f"feishu:course:{legacy_hash}", status="succeeded").first()
            if legacy:
                prior_phase = "preview" if course.enroll_start and legacy.created_at < course.enroll_start else "open"
                if prior_phase == material:
                    continue
        key = hashlib.sha256(f"{target}:{course.id}:{event_type}:{material}".encode()).hexdigest()
        jobs.append(enqueue_notification_job(
            session, channel="feishu", course_ids=[course.id], event_type=event_type,
            delivery_mode=delivery_mode, job_type="daily_summary" if daily else "course_push",
            idempotency_key=f"feishu:course:{key}", payload={"target": target, "summary_date": summary_date},
            priority=50, reset_terminal_failure=False,
        ))
    return jobs


def _target_matches(job) -> bool:
    url, _ = _credential_config()
    return job.payload.get("target") == hashlib.sha256(url.encode()).hexdigest()[:16]


async def deliver_job(job) -> NotificationDeliveryResult:
    """Recheck current scope, group identity, lifecycle and global preferences."""
    from src.models import Course, get_session, PushLog
    from src.filters import filter_courses, load_filter_config
    if job.job_type == "system_alert":
        return NotificationDeliveryResult(True, message="Legacy operations event excluded from student group")
    kind = "courses"
    if not enabled(kind):
        return NotificationDeliveryResult(False, message="Feishu scope disabled")
    if not _target_matches(job):
        return NotificationDeliveryResult(True, message="Webhook group changed; old task skipped")
    session = get_session()
    try:
        course = session.query(Course).filter_by(id=(job.course_ids or [None])[0]).first()
        if not course or is_course_expired(course) or not filter_courses([course], load_filter_config()):
            return NotificationDeliveryResult(True, message="Course expired or filtered; skipped")
        if not course.last_seen or business_now() - course.last_seen > timedelta(minutes=10):
            return NotificationDeliveryResult(True, message="Course snapshot stale; skipped")
        if job.event_type == "snipe" and (not course.enroll_start or not course.enroll_end or course.enroll_start > business_now()):
            return NotificationDeliveryResult(True, message="Reopened course is not open; skipped")
        if job.job_type == "daily_summary" and job.payload.get("summary_date") != business_now().strftime("%Y-%m-%d"):
            return NotificationDeliveryResult(True, message="Old daily summary skipped")
        card = _course_card(course, job.event_type, job.delivery_mode)
    finally:
        session.close()
    ok = await send_card(card, kind)
    if ok and kind == "courses":
        session = get_session()
        try:
            session.add(PushLog(course_id=job.course_ids[0], push_type="feishu", success=True))
            session.commit()
        finally:
            session.close()
    return NotificationDeliveryResult(ok, delivered_count=1 if ok else 0,
                                      message="Feishu accepted" if ok else "Feishu delivery failed")


async def drain_jobs(*, limit=20):
    types = []
    for kind, job_types in _KINDS.items():
        if enabled(kind):
            types.extend(job_types)
    if not types:
        return {"claimed": 0, "succeeded": 0, "failed": 0, "delivered_count": 0}
    return await drain_notification_jobs({"feishu": deliver_job}, limit=limit, job_types=types)
