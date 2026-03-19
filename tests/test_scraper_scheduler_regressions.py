import unittest
from datetime import datetime, timedelta
import sys
import types
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Course
from src.scraper import (
    _cleanup_near_duplicate_courses,
    _collect_current_view_courses,
    _dedupe_scraped_courses,
    _find_similar_active_course,
    _is_near_duplicate_triplet,
    generate_course_id,
    generate_legacy_course_id,
    save_courses_to_db,
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

    def test_course_id_distinguishes_parallel_offerings(self):
        common = {
            "name": "北航红船星河宣讲团·周末理论课堂",
            "start_time": "2026-03-21 15:00",
            "enroll_start": "2026-03-19 12:00",
            "teacher": "王政鑫 王葳",
        }
        id_a = generate_course_id(location="学院路主M201", campus="学院路校区", **common)
        id_b = generate_course_id(location="沙河SH3-101", campus="沙河校区", **common)
        self.assertNotEqual(id_a, id_b)

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

    def test_save_courses_to_db_keeps_parallel_offerings_separate(self):
        now = datetime.now()
        legacy_id = generate_legacy_course_id(
            "课程D",
            "2026-03-22 19:00",
            "2026-03-20 12:00",
            "赵老师",
        )
        session = self.Session()
        try:
            session.add(
                Course(
                    id=legacy_id,
                    name="课程D",
                    teacher="赵老师",
                    location="学院路主M201",
                    campus="学院路校区",
                    start_time=datetime(2026, 3, 22, 19, 0),
                    end_time=datetime(2026, 3, 22, 20, 0),
                    enroll_start=datetime(2026, 3, 20, 12, 0),
                    enroll_end=datetime(2026, 3, 22, 18, 0),
                    capacity=120,
                    enrolled=120,
                    last_seen=now,
                    expired=False,
                )
            )
            session.commit()
        finally:
            session.close()

        payload = [
            {
                "id": generate_course_id("课程D", "2026-03-22 19:00", "2026-03-20 12:00", "赵老师", "学院路主M201", "学院路校区"),
                "legacy_id": legacy_id,
                "name": "课程D",
                "teacher": "赵老师",
                "location": "学院路主M201",
                "campus": "学院路校区",
                "category": "博雅课程-德育",
                "start_time": "2026-03-22 19:00",
                "end_time": "2026-03-22 20:00",
                "enroll_start": "2026-03-20 12:00",
                "enroll_end": "2026-03-22 18:00",
                "capacity": 120,
                "enrolled": 119,
                "status": "预告",
            },
            {
                "id": generate_course_id("课程D", "2026-03-22 19:00", "2026-03-20 12:00", "赵老师", "沙河SH3-101", "沙河校区"),
                "legacy_id": legacy_id,
                "name": "课程D",
                "teacher": "赵老师",
                "location": "沙河SH3-101",
                "campus": "沙河校区",
                "category": "博雅课程-德育",
                "start_time": "2026-03-22 19:00",
                "end_time": "2026-03-22 20:00",
                "enroll_start": "2026-03-20 12:00",
                "enroll_end": "2026-03-22 18:00",
                "capacity": 120,
                "enrolled": 118,
                "status": "预告",
            },
        ]

        with patch("src.scraper.get_session", side_effect=lambda: self.Session()):
            new_ids = save_courses_to_db(payload)

        verify = self.Session()
        try:
            rows = verify.query(Course).filter(Course.name == "课程D").order_by(Course.location).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(len(new_ids), 1)
            self.assertEqual(rows[0].remaining, 1)
            self.assertEqual(rows[1].remaining, 2)
        finally:
            verify.close()

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

    def test_dedupe_scraped_courses_preserves_preview_snapshot_from_other_view(self):
        course_id = generate_course_id(
            "课程预告A",
            "2026-03-28 14:30",
            "2026-03-23 18:00",
            "许天宇",
            "学院路校区主M202",
            "全部校区",
        )
        rows = [
            {
                "id": course_id,
                "name": "课程预告A",
                "category": "博雅课程-劳动教育",
                "location": "学院路校区主M202",
                "teacher": "许天宇",
                "start_time": "2026-03-28 14:30",
                "end_time": "2026-03-28 15:45",
                "enroll_start": "2026-03-23 18:00",
                "enroll_end": "2026-03-28 12:00",
                "capacity": 0,
                "enrolled": 0,
                "status": "",
                "__row_index": 0,
                "__table_index": 0,
            },
            {
                "id": course_id,
                "name": "课程预告A",
                "category": "博雅课程-劳动教育",
                "location": "学院路校区主M202",
                "teacher": "许天宇",
                "start_time": "2026-03-28 14:30",
                "end_time": "2026-03-28 15:45",
                "enroll_start": "2026-03-23 18:00",
                "enroll_end": "2026-03-28 12:00",
                "capacity": 250,
                "enrolled": 0,
                "status": "预告",
                "__row_index": 3,
                "__table_index": 1,
            },
        ]

        deduped = _dedupe_scraped_courses(rows)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["status"], "预告")
        self.assertEqual(deduped[0]["capacity"], 250)
        self.assertEqual(deduped[0]["enrolled"], 0)

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


class ScraperAsyncRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_collect_current_view_courses_stops_after_consecutive_empty_pages(self):
        page = object()

        with (
            patch("src.scraper._ensure_session_with_retry", new=AsyncMock(return_value=True)),
            patch("src.scraper._parse_visible_course_tables", new=AsyncMock(side_effect=[[], []])),
            patch("src.scraper._enrich_with_details", new=AsyncMock()),
            patch("src.scraper._go_to_next_page", new=AsyncMock(return_value=True)) as next_page_mock,
        ):
            courses = await _collect_current_view_courses(page, include_details=False, view_name="default")

        self.assertEqual(courses, [])
        self.assertEqual(next_page_mock.await_count, 1)


if __name__ == "__main__":
    unittest.main()
