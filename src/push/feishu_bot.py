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
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
import uuid

from loguru import logger

from src.course_state import get_check_in_display_label, is_course_expired
from src.notification_jobs import NotificationDeliveryResult, drain_notification_jobs, enqueue_notification_job
from src.runtime import is_staging
from src.time_utils import now as business_now

_transport_lock = threading.Lock()
_last_request_at = 0.0
_MAX_BODY_BYTES = 19000
_KINDS = {"alerts": {"system_alert"}, "courses": {"course_push", "daily_summary"}}


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
    return {"configured": configured, "alerts_enabled": enabled("alerts"),
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
    labels = {"snipe": "退课补位", "active_watch": "报名动态"}
    title = "每日课程汇总" if delivery_mode == "digest_daily" else labels.get(event_type, "课程提醒")
    when = lambda value: value.strftime("%Y-%m-%d %H:%M") if value else "待确认"
    places = f"{course.remaining} / {course.capacity}" if course.capacity and course.capacity > 0 else "待确认"
    fields = [("剩余 / 容量", places), ("签到方式", get_check_in_display_label(course)),
              ("适用校区", course.campus), ("课程类别", course.category)]
    elements = [
        {"tag": "div", "text": _plain(course.name)},
        {"tag": "div", "fields": [{"is_short": True, "text": _plain(f"{label}\n{value or '待确认'}")}
                                  for label, value in fields]},
        {"tag": "hr"},
        {"tag": "div", "text": _plain(f"报名时间\n{when(course.enroll_start)} — {when(course.enroll_end)}", 240)},
        {"tag": "div", "text": _plain(f"上课时间\n{when(course.start_time)} — {when(course.end_time)}", 240)},
        {"tag": "div", "text": _plain(f"上课地点\n{course.location or '待确认'}", 240)},
        {"tag": "action", "actions": [{"tag": "button", "type": "primary",
            "text": _plain("查看课程列表"), "url": "https://buaaboya.top/#courses"}]},
        {"tag": "note", "elements": [_plain(f"采集时间：{when(course.last_seen)}（北京时间）\n名额以官方选课系统为准。", 240)]},
    ]
    return _card(f"博雅 · {title}", "orange" if event_type == "snipe" else "blue", elements)


def _alert_card(job) -> dict:
    failed = job.event_type == "failure"
    statuses = {"auth_expired": "登录失效", "upstream_unavailable": "上游不可用", "parse_failed": "页面解析异常",
                "timeout": "响应超时", "browser_unavailable": "采集浏览器不可用", "task_failed": "采集任务失败",
                "scrape_failed": "采集失败", "success_empty": "空快照待核对"}
    title = "博雅系统告警" if failed else "博雅系统恢复通知"
    text = (f"课程抓取连续失败：{job.payload.get('failures', 0)} 次\n"
            f"状态：{statuses.get(job.payload.get('status'), '采集失败')}" if failed else "课程抓取已恢复成功。")
    return _card(title, "red" if failed else "green", [
        {"tag": "div", "text": _plain(text, 240)},
        {"tag": "div", "text": _plain(f"{'检测' if failed else '恢复'}时间：{job.created_at:%Y-%m-%d %H:%M:%S}（北京时间）")},
        {"tag": "note", "elements": [_plain("请由管理员核对采集状态。" if failed else "此前告警已恢复，后续继续观察。")]}])


def _course_text(course, event_type: str, delivery_mode: str) -> str:
    labels = {"snipe": "退课补位", "active_watch": "报名动态"}
    title = "每日课程汇总" if delivery_mode == "digest_daily" else labels.get(event_type, "课程提醒")
    when = lambda value: value.strftime("%m-%d %H:%M") if value else "待确认"
    # Plain text content avoids markdown injection and includes the optional
    # Feishu keyword requirement through the fixed 博雅 prefix.
    return (f"博雅{title}\n{str(course.name)[:180]}\n"
            f"校区：{str(course.campus or '待确认')[:60]}｜类别：{str(course.category or '待确认')[:60]}\n"
            f"签到：{get_check_in_display_label(course)}｜剩余：{course.remaining}/{course.capacity}\n"
            f"报名：{when(course.enroll_start)} — {when(course.enroll_end)}\n"
            f"上课：{when(course.start_time)}\n地点：{str(course.location or '待确认')[:180]}\n"
            f"查看课程：https://buaaboya.top/#courses\n"
            "名额以官方选课系统为准。")


def enqueue_courses(session, courses, *, event_type="new", delivery_mode="instant", summary_date=""):
    if not enabled("courses"):
        return []
    jobs = []
    for course in courses:
        daily = delivery_mode == "digest_daily"
        # One course per task avoids duplicate successful recipients when one
        # part of a batch fails. Recipient is a hash of the configured group URL.
        url, _ = _credential_config()
        target = hashlib.sha256(url.encode()).hexdigest()[:16]
        material = f"day:{summary_date}" if daily else (
            "new" if event_type == "new" else f"remaining:{course.remaining}"
        )
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
    from src.models import Course, NotificationJob, get_session, PushLog
    from src.filters import filter_courses, load_filter_config
    kind = "alerts" if job.job_type == "system_alert" else "courses"
    if not enabled(kind):
        return NotificationDeliveryResult(False, message="Feishu scope disabled")
    if not _target_matches(job):
        return NotificationDeliveryResult(True, message="Webhook group changed; old task skipped")
    if job.job_type == "system_alert":
        if business_now() - job.created_at > timedelta(hours=2):
            return NotificationDeliveryResult(True, message="Expired system alert skipped")
        payload = job.payload
        if job.event_type == "failure":
            # An incident recovered before delivery should not emit stale alarms.
            session = get_session()
            try:
                recovered = session.query(NotificationJob).filter_by(
                    idempotency_key=f"feishu:recovery:{payload['incident']}"
                ).first()
                if recovered:
                    return NotificationDeliveryResult(True, message="Incident already recovered")
            finally:
                session.close()
        card = _alert_card(job)
    else:
        session = get_session()
        try:
            course = session.query(Course).filter_by(id=(job.course_ids or [None])[0]).first()
            if not course or is_course_expired(course) or not filter_courses([course], load_filter_config()):
                return NotificationDeliveryResult(True, message="Course expired or filtered; skipped")
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


def queue_scrape_state(*, recovered=False, failures=0, status="scrape_failed", cooldown_minutes=60):
    """Persist alert/recovery and cooldown across restarts without raw errors."""
    from src.models import NotificationJob, get_session
    if not enabled("alerts"):
        return
    session = get_session()
    try:
        url, _ = _credential_config()
        target = hashlib.sha256(url.encode()).hexdigest()[:16]
        latest = session.query(NotificationJob).filter_by(
            channel="feishu", job_type="system_alert", event_type="failure"
        ).order_by(NotificationJob.id.desc()).first()
        if latest and latest.payload.get("target") != target:
            latest = None
        incident = latest.payload.get("incident") if latest else None
        recovery = session.query(NotificationJob).filter_by(
            idempotency_key=f"feishu:recovery:{incident}"
        ).first() if incident else None
        if recovered and (not latest or recovery):
            return
        now = business_now()
        if not recovered and latest and not recovery and now - latest.created_at < timedelta(minutes=cooldown_minutes):
            return
        if not incident or (recovery and not recovered):
            incident = uuid.uuid4().hex
        safe_status = status if status in {
            "auth_expired", "upstream_unavailable", "parse_failed", "timeout",
            "browser_unavailable", "task_failed", "scrape_failed", "success_empty",
        } else "scrape_failed"
        enqueue_notification_job(
            session, channel="feishu", course_ids=["system:scrape"], job_type="system_alert",
            event_type="recovery" if recovered else "failure", delivery_mode="status", priority=100,
            idempotency_key=(f"feishu:recovery:{incident}" if recovered
                             else f"feishu:failure:{incident}:{now.isoformat()}"),
            payload={"incident": incident, "target": target, "failures": int(failures), "status": safe_status},
            reset_terminal_failure=False,
        )
        session.commit()
    finally:
        session.close()
