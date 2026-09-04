"""SQLite outbox：通知任务的幂等、领取、重试和完成状态管理。"""

import asyncio
import inspect
import json
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any, Callable, Dict, Iterable, Optional

from loguru import logger
from sqlalchemy import and_, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.models import NotificationJob, get_session, commit_with_retry
from src.time_utils import now as business_now


class NotificationJobStatus:
    """任务状态常量，避免业务代码散落字符串字面量。"""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class NotificationDeliveryResult:
    """一个通知任务的投递结果。"""

    success: bool
    delivered_count: int = 0
    message: str = ""


def build_notification_idempotency_key(
    *,
    channel: str,
    recipient: str = "global",
    course_ids: Iterable[str],
    event_type: str,
    delivery_mode: str,
    job_type: str = "course_push",
    dedupe_material: str = "",
) -> str:
    """生成不含原始邮箱的稳定幂等键。"""

    normalized_ids = sorted({str(course_id) for course_id in course_ids if course_id})
    fingerprint_input = "\n".join(normalized_ids + [dedupe_material or ""])
    fingerprint = sha256(fingerprint_input.encode("utf-8")).hexdigest()[:32]
    safe_recipient = str(recipient or "global")[:80]
    return ":".join(
        [
            job_type or "course_push",
            channel or "unknown",
            safe_recipient,
            event_type or "new",
            delivery_mode or "instant",
            fingerprint,
        ]
    )


def enqueue_notification_job(
    session,
    *,
    channel: str,
    course_ids: Iterable[str],
    event_type: str = "new",
    delivery_mode: str = "instant",
    subscriber_id: Optional[int] = None,
    subscriber_email: str = "",
    job_type: str = "course_push",
    priority: int = 0,
    payload: Optional[Dict[str, Any]] = None,
    dedupe_material: str = "",
    idempotency_key: Optional[str] = None,
    max_attempts: int = 3,
    available_at=None,
) -> NotificationJob:
    """幂等创建通知任务；重复请求只返回已有任务。"""

    normalized_ids = list(dict.fromkeys(str(course_id) for course_id in course_ids if course_id))
    if not normalized_ids:
        raise ValueError("notification job requires at least one course id")

    recipient = str(subscriber_id) if subscriber_id is not None else "global"
    key = idempotency_key or build_notification_idempotency_key(
        channel=channel,
        recipient=recipient,
        course_ids=normalized_ids,
        event_type=event_type,
        delivery_mode=delivery_mode,
        job_type=job_type,
        dedupe_material=dedupe_material,
    )
    now = business_now()
    existing = (
        session.query(NotificationJob)
        .filter(NotificationJob.idempotency_key == key)
        .one_or_none()
    )
    if existing:
        # 同一业务信号在达到重试上限后再次出现时，允许新一轮有限重试；
        # 成功任务始终不会被重置，避免重复成功投递。
        if (
            existing.status == NotificationJobStatus.FAILED
            and int(existing.attempts or 0) >= int(existing.max_attempts or 1)
        ):
            existing.status = NotificationJobStatus.PENDING
            existing.attempts = 0
            existing.available_at = now
            existing.locked_at = None
            existing.completed_at = None
            existing.last_error = ""
            existing.updated_at = now
            session.flush()
        return existing

    # 任务表是 SQLite outbox；使用原生冲突忽略避免并发重复创建时
    # 回滚调用方事务中的其他改动。
    session.execute(
        sqlite_insert(NotificationJob)
        .values(
            idempotency_key=key,
            job_type=job_type,
            channel=channel,
            subscriber_id=subscriber_id,
            subscriber_email=(subscriber_email or "").strip().lower(),
            course_ids_json=json.dumps(normalized_ids, ensure_ascii=False),
            event_type=event_type,
            delivery_mode=delivery_mode,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            priority=int(priority or 0),
            status=NotificationJobStatus.PENDING,
            attempts=0,
            max_attempts=max(1, int(max_attempts or 1)),
            available_at=available_at or now,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    return (
        session.query(NotificationJob)
        .filter(NotificationJob.idempotency_key == key)
        .one()
    )


def claim_next_notification_job(
    session,
    *,
    channels: Optional[Iterable[str]] = None,
    worker_id: str = "",
    lease_seconds: int = 180,
) -> Optional[NotificationJob]:
    """原子地领取一条到期任务，并把它标记为处理中。"""

    now = business_now()
    stale_before = now - timedelta(seconds=max(30, int(lease_seconds)))
    ready_time = or_(
        NotificationJob.available_at.is_(None),
        NotificationJob.available_at <= now,
    )
    claimable = or_(
        and_(
            NotificationJob.status.in_([NotificationJobStatus.PENDING, NotificationJobStatus.FAILED]),
            ready_time,
            NotificationJob.attempts < NotificationJob.max_attempts,
        ),
        and_(
            NotificationJob.status == NotificationJobStatus.PROCESSING,
            NotificationJob.locked_at.is_not(None),
            NotificationJob.locked_at <= stale_before,
        ),
    )

    query = session.query(NotificationJob).filter(claimable)
    normalized_channels = [str(channel) for channel in (channels or []) if channel]
    if normalized_channels:
        query = query.filter(NotificationJob.channel.in_(normalized_channels))
    while True:
        candidate = (
            query.order_by(
                NotificationJob.priority.desc(),
                NotificationJob.created_at.asc(),
                NotificationJob.id.asc(),
            )
            .first()
        )
        if not candidate:
            return None

        # 进程可能在最后一次尝试已领取后崩溃。此时不能再次递增 attempts，
        # 但也不能让 processing 永久悬挂，直接收敛为终态失败后继续找下一条。
        if (
            candidate.status == NotificationJobStatus.PROCESSING
            and int(candidate.attempts or 0) >= int(candidate.max_attempts or 1)
        ):
            stale_update = (
                session.query(NotificationJob)
                .filter(
                    NotificationJob.id == candidate.id,
                    NotificationJob.status == NotificationJobStatus.PROCESSING,
                    NotificationJob.locked_at == candidate.locked_at,
                )
                .update(
                    {
                        NotificationJob.status: NotificationJobStatus.FAILED,
                        NotificationJob.locked_at: None,
                        NotificationJob.available_at: None,
                        NotificationJob.completed_at: None,
                        NotificationJob.last_error: "processing lease expired after max attempts",
                        NotificationJob.updated_at: now,
                    },
                    synchronize_session=False,
                )
            )
            if stale_update:
                commit_with_retry(session)
            else:
                session.rollback()
            continue
        break

    previous_status = candidate.status
    update_query = session.query(NotificationJob).filter(
        NotificationJob.id == candidate.id,
        NotificationJob.status == previous_status,
    )
    if previous_status == NotificationJobStatus.PROCESSING:
        update_query = update_query.filter(NotificationJob.locked_at == candidate.locked_at)

    updated = update_query.update(
        {
            NotificationJob.status: NotificationJobStatus.PROCESSING,
            NotificationJob.attempts: candidate.attempts + 1,
            NotificationJob.locked_at: now,
            NotificationJob.updated_at: now,
            NotificationJob.last_error: "",
        },
        synchronize_session=False,
    )
    if updated != 1:
        session.rollback()
        return None

    commit_with_retry(session)
    session.refresh(candidate)
    session.expunge(candidate)
    logger.debug(
        "notification job claimed: id={} channel={} worker={} attempt={}",
        candidate.id,
        candidate.channel,
        worker_id or "default",
        candidate.attempts,
    )
    return candidate


def mark_notification_job_success(session, job_id: int, message: str = "") -> bool:
    """把任务标记为成功，清理租约。"""

    now = business_now()
    updated = (
        session.query(NotificationJob)
        .filter(NotificationJob.id == job_id)
        .update(
            {
                NotificationJob.status: NotificationJobStatus.SUCCEEDED,
                NotificationJob.completed_at: now,
                NotificationJob.locked_at: None,
                NotificationJob.available_at: None,
                NotificationJob.last_error: message or "",
                NotificationJob.updated_at: now,
            },
            synchronize_session=False,
        )
    )
    commit_with_retry(session)
    return updated == 1


def mark_notification_job_failure(
    session,
    job_id: int,
    error: str,
    *,
    retry_base_seconds: int = 30,
) -> bool:
    """记录失败并安排指数退避；超过上限后保留为终态失败。"""

    job = session.query(NotificationJob).filter(NotificationJob.id == job_id).first()
    if not job:
        return False

    now = business_now()
    attempts = int(job.attempts or 0)
    retryable = attempts < int(job.max_attempts or 1)
    delay = max(0, int(retry_base_seconds)) * (2 ** max(0, attempts - 1))
    safe_error = str(error or "notification delivery failed").strip()[:1000]
    job.status = NotificationJobStatus.FAILED
    job.locked_at = None
    job.completed_at = None
    job.available_at = now + timedelta(seconds=delay) if retryable else None
    job.last_error = safe_error
    job.updated_at = now
    commit_with_retry(session)
    return True


async def drain_notification_jobs(
    handlers: Dict[str, Callable[[NotificationJob], Any]],
    *,
    limit: int = 20,
    worker_id: str = "",
    lease_seconds: int = 180,
) -> dict:
    """领取并执行有限数量的任务，供即时推送和定时恢复共同使用。"""

    normalized_limit = max(1, min(100, int(limit)))
    worker_id = worker_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    result = {
        "claimed": 0,
        "succeeded": 0,
        "failed": 0,
        "delivered_count": 0,
    }
    if not handlers:
        return result

    for _ in range(normalized_limit):
        claim_session = get_session()
        try:
            job = claim_next_notification_job(
                claim_session,
                channels=handlers.keys(),
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
        except Exception:
            claim_session.rollback()
            raise
        finally:
            claim_session.close()

        if not job:
            break

        result["claimed"] += 1
        handler = handlers.get(job.channel)
        delivery = NotificationDeliveryResult(False, message=f"unsupported channel: {job.channel}")
        try:
            raw_result = handler(job) if handler else delivery
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result
            if isinstance(raw_result, NotificationDeliveryResult):
                delivery = raw_result
            elif isinstance(raw_result, bool):
                delivery = NotificationDeliveryResult(raw_result, 1 if raw_result else 0)
            else:
                delivery = NotificationDeliveryResult(bool(raw_result), 0)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.exception("notification job handler failed: id={}", job.id)
            delivery = NotificationDeliveryResult(False, message=str(error))

        finish_session = get_session()
        try:
            if delivery.success:
                mark_notification_job_success(finish_session, job.id, delivery.message)
                result["succeeded"] += 1
                result["delivered_count"] += max(0, int(delivery.delivered_count or 0))
            else:
                mark_notification_job_failure(finish_session, job.id, delivery.message or "notification delivery failed")
                result["failed"] += 1
        except Exception:
            finish_session.rollback()
            logger.exception("notification job state update failed: id={}", job.id)
        finally:
            finish_session.close()

    return result
