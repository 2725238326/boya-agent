"""
Shared course state helpers used by scraping, scheduling, and UI serialization.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from src.time_utils import now as business_now


HOT_COURSE_REMAINING_THRESHOLD = max(1, int(os.getenv("HOT_COURSE_REMAINING_THRESHOLD", "3")))
HOT_COURSE_FILL_RATIO = min(0.98, max(0.6, float(os.getenv("HOT_COURSE_FILL_RATIO", "0.82"))))


def _read_attr(course: Any, name: str, default: Any = None) -> Any:
    if isinstance(course, dict):
        return course.get(name, default)
    return getattr(course, name, default)


def course_fill_ratio(course: Any = None, *, capacity: Optional[int] = None, enrolled: Optional[int] = None) -> float:
    if course is not None:
        capacity = _read_attr(course, "capacity", capacity)
        enrolled = _read_attr(course, "enrolled", enrolled)
    capacity_value = max(0, int(capacity or 0))
    if capacity_value <= 0:
        return 0.0
    enrolled_value = max(0, int(enrolled or 0))
    return min(1.0, enrolled_value / capacity_value)


def course_remaining(course: Any = None, *, capacity: Optional[int] = None, enrolled: Optional[int] = None) -> int:
    if course is not None:
        capacity = _read_attr(course, "capacity", capacity)
        enrolled = _read_attr(course, "enrolled", enrolled)
    return max(0, int(capacity or 0) - int(enrolled or 0))


def get_check_in_display_label(course: Any) -> str:
    """Return a stable user-facing check-in label.

    `sign_method` reflects enrollment mode such as "直接选课", which should not
    be shown as the course's check-in badge. For UI and notifications we only
    expose the normalized sign-in state: "自主签到" or "常规签到".
    """
    raw_check_in = str(_read_attr(course, "check_in_method", "") or "").strip()
    raw_sign_method = str(_read_attr(course, "sign_method", "") or "").strip()
    combined = f"{raw_check_in} {raw_sign_method}"
    if "自主" in combined or "自选" in combined:
        return "自主签到"
    return "常规签到"


def is_self_check_in(course: Any) -> bool:
    return get_check_in_display_label(course) == "自主签到"


def is_course_finished(course: Any, now: Optional[datetime] = None) -> bool:
    now = now or business_now()
    end_time = _read_attr(course, "end_time")
    return bool(end_time and end_time <= now)


def is_course_expired(course: Any, now: Optional[datetime] = None) -> bool:
    """判断课程是否已经不应再参与报名或公开展示。"""
    now = now or business_now()
    enroll_end = _read_attr(course, "enroll_end")
    return bool(
        _read_attr(course, "expired", False)
        or is_course_finished(course, now)
        or (enroll_end and enroll_end <= now)
    )


def is_enrollment_closed(course: Any, now: Optional[datetime] = None) -> bool:
    """判断报名窗口是否已经结束；供筛选、接口和通知共同使用。"""
    return is_course_expired(course, now)


def is_enrollment_open(course: Any, now: Optional[datetime] = None) -> bool:
    now = now or business_now()
    enroll_start = _read_attr(course, "enroll_start")
    enroll_end = _read_attr(course, "enroll_end")
    if enroll_start and enroll_start > now:
        return False
    if enroll_end and enroll_end <= now:
        return False
    if is_course_expired(course, now):
        return False
    return True


def get_hot_reason(
    course: Any,
    now: Optional[datetime] = None,
    *,
    remaining_threshold: int = HOT_COURSE_REMAINING_THRESHOLD,
    fill_ratio_threshold: float = HOT_COURSE_FILL_RATIO,
) -> str:
    if not is_enrollment_open(course, now):
        return ""
    if int(_read_attr(course, "capacity", 0) or 0) <= 0:
        return ""

    remaining = course_remaining(course)
    if remaining <= remaining_threshold:
        return "remaining"
    if course_fill_ratio(course) >= fill_ratio_threshold:
        return "fill_ratio"
    return ""


def is_hot_course(
    course: Any,
    now: Optional[datetime] = None,
    *,
    remaining_threshold: int = HOT_COURSE_REMAINING_THRESHOLD,
    fill_ratio_threshold: float = HOT_COURSE_FILL_RATIO,
) -> bool:
    return bool(
        get_hot_reason(
            course,
            now,
            remaining_threshold=remaining_threshold,
            fill_ratio_threshold=fill_ratio_threshold,
        )
    )
