import unittest
from datetime import datetime, timedelta
import sys
import types
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Course
from src.scraper import (
    _cleanup_near_duplicate_courses,
    _find_similar_active_course,
    _is_near_duplicate_triplet,
)


apscheduler_module = types.ModuleType("apscheduler")
apscheduler_schedulers = types.ModuleType("apscheduler.schedulers")
apscheduler_asyncio = types.ModuleType("apscheduler.schedulers.asyncio")
apscheduler_triggers = types.ModuleType("apscheduler.triggers")
apscheduler_interval = types.ModuleType("apscheduler.triggers.interval")
apscheduler_cron = types.ModuleType("apscheduler.triggers.cron")


class _DummyScheduler:
    def add_job(self, *args, **kwargs):
        return None

    def start(self):
        return None

    def reschedule_job(self, *args, **kwargs):
        return None

    def get_job(self, *args, **kwargs):
        return None

    def remove_job(self, *args, **kwargs):
        return None


class _DummyTrigger:
    def __init__(self, *args, **kwargs):
        pass


apscheduler_asyncio.AsyncIOScheduler = _DummyScheduler
apscheduler_interval.IntervalTrigger = _DummyTrigger
apscheduler_cron.CronTrigger = _DummyTrigger

sys.modules.setdefault("apscheduler", apscheduler_module)
sys.modules.setdefault("apscheduler.schedulers", apscheduler_schedulers)
sys.modules.setdefault("apscheduler.schedulers.asyncio", apscheduler_asyncio)
sys.modules.setdefault("apscheduler.triggers", apscheduler_triggers)
sys.modules.setdefault("apscheduler.triggers.interval", apscheduler_interval)
sys.modules.setdefault("apscheduler.triggers.cron", apscheduler_cron)

from src import scheduler


class ScraperSchedulerRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_same_day_one_hour_drift_is_still_treated_as_near_duplicate(self):
        base = datetime(2026, 3, 20, 10, 0)
        self.assertTrue(
            _is_near_duplicate_triplet(
                base,
                base + timedelta(hours=1),
                base + timedelta(days=1),
                base + timedelta(days=1, hours=1),
                base + timedelta(days=1, hours=2),
                base + timedelta(days=1, hours=3),
            )
        )

    def test_stale_hour_drift_record_is_not_reused_as_existing_course(self):
        now = datetime.now()
        session = self.Session()
        try:
            existing = Course(
                id="old-course",
                name="课程A",
                teacher="张三",
                location="教室1",
                campus="学院路",
                start_time=datetime(2026, 3, 20, 10, 0),
                end_time=datetime(2026, 3, 20, 12, 0),
                enroll_start=datetime(2026, 3, 19, 10, 0),
                enroll_end=datetime(2026, 3, 19, 12, 0),
                capacity=80,
                enrolled=80,
                last_seen=now - timedelta(hours=7),
                expired=False,
            )
            session.add(existing)
            session.commit()

            incoming = {
                "name": "课程A",
                "teacher": "张三",
                "location": "教室1",
                "campus": "学院路",
                "start_time": "2026-03-20 11:00",
                "enroll_start": "2026-03-19 11:00",
                "enroll_end": "2026-03-19 13:00",
            }

            matched = _find_similar_active_course(session, incoming, now)
            self.assertIsNone(matched)
        finally:
            session.close()

    def test_cleanup_prefers_snapshot_with_more_remaining_seats(self):
        now = datetime.now()
        session = self.Session()
        try:
            full_row = Course(
                id="row-full",
                name="课程B",
                teacher="李四",
                location="教室2",
                campus="沙河",
                start_time=datetime(2026, 3, 21, 10, 0),
                end_time=datetime(2026, 3, 21, 12, 0),
                enroll_start=datetime(2026, 3, 20, 10, 0),
                enroll_end=datetime(2026, 3, 20, 12, 0),
                capacity=60,
                enrolled=60,
                last_seen=now,
                expired=False,
            )
            reopened_row = Course(
                id="row-open",
                name="课程B",
                teacher="李四",
                location="教室2",
                campus="沙河",
                start_time=datetime(2026, 3, 21, 11, 0),
                end_time=datetime(2026, 3, 21, 13, 0),
                enroll_start=datetime(2026, 3, 20, 11, 0),
                enroll_end=datetime(2026, 3, 20, 13, 0),
                capacity=60,
                enrolled=59,
                last_seen=now,
                expired=False,
            )
            session.add_all([full_row, reopened_row])
            session.commit()

            _cleanup_near_duplicate_courses(session, now)
            session.commit()

            rows = session.query(Course).filter(Course.name == "课程B").all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].remaining, 1)
        finally:
            session.close()

    def test_sync_course_lifecycle_marks_expired_instead_of_deleting(self):
        now = datetime.now()
        session = self.Session()
        session.add(
            Course(
                id="ended-course",
                name="课程C",
                teacher="王五",
                location="教室3",
                campus="学院路",
                enroll_end=now - timedelta(hours=1),
                capacity=50,
                enrolled=50,
                expired=False,
            )
        )
        session.commit()
        session.close()

        scheduler._push_buffer["urgent"] = ["ended-course"]
        scheduler._push_buffer["soon"] = ["ended-course"]

        with patch("src.scheduler.get_session", side_effect=lambda: self.Session()):
            scheduler._sync_course_lifecycle()

        verify = self.Session()
        try:
            row = verify.query(Course).filter_by(id="ended-course").first()
            self.assertIsNotNone(row)
            self.assertTrue(row.expired)
            self.assertNotIn("ended-course", scheduler._push_buffer["urgent"])
            self.assertNotIn("ended-course", scheduler._push_buffer["soon"])
        finally:
            verify.close()


if __name__ == "__main__":
    unittest.main()
