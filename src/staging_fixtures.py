"""Deterministic course scenarios with dates relative to the business clock."""

import os
from datetime import timedelta

from src.runtime import validate_staging
from src.scrape_outcome import ScrapeOutcome, ScrapeStatus
from src.time_utils import now as business_now

SCENARIOS = ("healthy", "empty", "auth_expired", "upstream_unavailable", "parse_failed", "timeout")


def fixture_outcome() -> ScrapeOutcome:
    validate_staging()
    scenario = os.getenv("STAGING_SCENARIO", "healthy")
    if scenario not in SCENARIOS:
        raise ValueError("unknown staging scenario")
    if scenario == "empty":
        return ScrapeOutcome(ScrapeStatus.SUCCESS_EMPTY, metadata={"duration_ms": 0, "fixture": True})
    if scenario != "healthy":
        return ScrapeOutcome(scenario, message=f"fixture: {scenario}", metadata={"duration_ms": 0})
    now = business_now().replace(microsecond=0)
    courses = []
    for key, remaining, start in (("open", 5, -60), ("full", 0, -60), ("upcoming", 10, 30), ("ended", 5, -120)):
        courses.append({
            "id": f"fixture-{key}", "name": f"预发布样例 {key}", "category": "文化",
            "teacher": "样例教师", "campus": "学院路", "location": "样例教室",
            "check_in_method": "自主签到", "sign_method": "自主选课",
            "capacity": 20, "enrolled": 20 - remaining,
            "enroll_start": (now + timedelta(minutes=start)).strftime("%Y-%m-%d %H:%M:%S"),
            "enroll_end": (now + timedelta(hours=-1 if key == "ended" else 3)).strftime("%Y-%m-%d %H:%M:%S"),
            "start_time": (now + timedelta(days=-1 if key == "ended" else 1)).strftime("%Y-%m-%d %H:%M:%S"),
            "end_time": (now + timedelta(days=-1 if key == "ended" else 1, hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return ScrapeOutcome(ScrapeStatus.SUCCESS_WITH_COURSES, courses, metadata={"duration_ms": 0, "fixture": True})
