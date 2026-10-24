"""Telegram operations channel, separate from student/course notifications."""

import asyncio
from datetime import timedelta
import hashlib
import json
import os
import re
import uuid
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError

from loguru import logger
from src.notification_jobs import NotificationDeliveryResult, drain_notification_jobs, enqueue_notification_job
from src.runtime import is_staging
from src.time_utils import now as business_now
from src.push.feishu_bot import _NoRedirect

_STATUS_LABELS = {
    "auth_expired": "校园登录失效", "upstream_unavailable": "课程系统不可用", "parse_failed": "课程页面解析异常",
    "timeout": "采集超时", "browser_unavailable": "采集浏览器不可用", "task_failed": "采集任务失败",
    "scrape_failed": "采集失败", "success_empty": "空快照待核对", "service_unavailable": "应用健康检查失败",
    "source_stale": "成功采集已超时", "disk_pressure": "磁盘使用率过高",
}


def _credentials():
    token = os.getenv("TELEGRAM_OPS_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_OPS_CHAT_ID", "").strip()
    if not token and not chat:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token) or not re.fullmatch(r"-?\d+|@[A-Za-z0-9_]{5,32}", chat):
        raise ValueError("Telegram operations credentials missing or invalid")
    return token, chat


def readiness():
    try:
        _credentials()
        configured = True
    except ValueError:
        configured = False
    return {"configured": configured, "enabled": enabled() if configured else False,
            "staging_blocked": is_staging(), "role": "operations"}


def enabled():
    if is_staging() or os.getenv("TELEGRAM_OPS_ENABLED", "").lower() != "true":
        return False
    try:
        _credentials()
        return True
    except ValueError:
        return False


def _target():
    return hashlib.sha256("\n".join(_credentials()).encode()).hexdigest()[:16]


def _send_sync(text, *, probe=False):
    if is_staging() or (not probe and not enabled()):
        return False
    try:
        token, chat = _credentials()
        body = json.dumps({"chat_id": chat, "text": text[:3500], "disable_web_page_preview": True}, ensure_ascii=False).encode()
        proxy = (os.getenv("TELEGRAM_OPS_PROXY_URL") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "").strip()
        if proxy and not re.match(r"^https?://", proxy):
            return False
        opener = build_opener(ProxyHandler({"https": proxy} if proxy else {}), _NoRedirect())
        request = Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                          headers={"Content-Type": "application/json"})
        with opener.open(request, timeout=10) as response:
            raw = response.read(65537)
            if response.status != 200 or len(raw) > 65536:
                return False
            result = json.loads(raw)
        return isinstance(result, dict) and result.get("ok") is True
    except HTTPError as error:
        logger.warning("Telegram ops HTTP failure: status={}", error.code)
        error.close()
    except Exception as error:
        logger.warning("Telegram ops send failed: type={}", type(error).__name__)
    return False


def queue_state(*, recovered=False, failures=0, status="scrape_failed", cooldown_minutes=60, component="scrape"):
    if not enabled():
        return
    from src.models import NotificationJob, get_session
    session = get_session()
    try:
        target = _target()
        component = component if component in {"scrape", "source", "service", "disk"} else "scrape"
        marker = f"system:{component}"
        latest = session.query(NotificationJob).filter_by(channel="telegram_ops", job_type="system_alert", event_type="failure").filter(
            NotificationJob.course_ids_json == json.dumps([marker], ensure_ascii=False)
        ).order_by(NotificationJob.id.desc()).first()
        if latest and latest.payload.get("target") != target:
            latest = None
        incident = latest.payload.get("incident") if latest else None
        recovery = session.query(NotificationJob).filter_by(idempotency_key=f"ops:recovery:{incident}").first() if incident else None
        now = business_now()
        if recovered and (not latest or recovery):
            return
        if not recovered and latest and not recovery and now - latest.created_at < timedelta(minutes=cooldown_minutes):
            return
        if not incident or (recovery and not recovered):
            incident = uuid.uuid4().hex
        enqueue_notification_job(session, channel="telegram_ops", course_ids=[marker], job_type="system_alert",
            event_type="recovery" if recovered else "failure", delivery_mode="status", priority=100,
            idempotency_key=f"ops:recovery:{incident}" if recovered else f"ops:failure:{incident}:{now.isoformat()}",
            payload={"target": target, "incident": incident, "failures": int(failures), "component": component,
                     "status": status if status in _STATUS_LABELS else "scrape_failed"}, reset_terminal_failure=False)
        session.commit()
    finally:
        session.close()


async def deliver_job(job):
    if not enabled():
        return NotificationDeliveryResult(False, message="Operations channel disabled")
    if job.payload.get("target") != _target() or business_now() - job.created_at > timedelta(hours=2):
        return NotificationDeliveryResult(True, message="Old operations task skipped")
    from src.models import NotificationJob, get_session
    if job.event_type == "failure":
        session = get_session()
        try:
            if session.query(NotificationJob).filter_by(idempotency_key=f"ops:recovery:{job.payload['incident']}").first():
                return NotificationDeliveryResult(True, message="Recovered incident skipped")
        finally:
            session.close()
    failed = job.event_type == "failure"
    component = {"scrape": "课程采集", "source": "采集时效", "service": "应用存活", "disk": "磁盘空间"}.get(job.payload.get("component"), "课程采集")
    text = (f"博雅运维 · {'异常告警' if failed else '恢复通知'}\n检查项：{component}\n"
            + (f"状态：{_STATUS_LABELS.get(job.payload.get('status'), '采集失败')}\n连续失败：{job.payload.get('failures', 0)} 次\n" if failed else "状态：检查已恢复正常\n")
            + f"检测时间：{job.created_at:%Y-%m-%d %H:%M:%S}（北京时间）\n管理后台：https://buaaboya.top/admin")
    ok = await asyncio.to_thread(_send_sync, text)
    return NotificationDeliveryResult(ok, delivered_count=1 if ok else 0, message="Telegram ops accepted" if ok else "Telegram ops delivery failed")


async def drain_jobs():
    if not enabled():
        return {"claimed": 0, "succeeded": 0, "failed": 0, "delivered_count": 0}
    return await drain_notification_jobs({"telegram_ops": deliver_job}, limit=3, job_types=["system_alert"])
