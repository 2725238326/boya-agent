import asyncio
import unittest
from datetime import datetime, timedelta
import sys
import types
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.course_state import get_check_in_display_label, is_self_check_in
from src.models import Base, Course, CourseReminder, EmailSubscriber, FilterConfig, NotificationJob
from src.scrape_outcome import ScrapeOutcome, ScrapeStatus
from src.scraper import (
    _extract_courses_from_network_payload,
    _build_course_row_payload,
    _cleanup_near_duplicate_courses,
    _collect_current_view_courses,
    _course_page_has_empty_state,
    _dedupe_scraped_courses,
    _find_similar_active_course,
    _is_near_duplicate_triplet,
    _parse_visible_course_tables,
    _select_best_header_row,
    _wait_course_tables_ready,
    assess_scrape_health,
    generate_course_id,
    generate_legacy_course_id,
    parse_datetime,
    save_courses_to_db,
)
from src.time_utils import now as business_now


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
        now = business_now()
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
        now = business_now()
        course_day = now + timedelta(days=2)
        enroll_day = now + timedelta(days=1)
        start_str = course_day.replace(hour=19, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        end_str = course_day.replace(hour=20, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        enroll_start_str = enroll_day.replace(hour=12, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        enroll_end_str = course_day.replace(hour=18, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        legacy_id = generate_legacy_course_id(
            "课程D",
            start_str,
            enroll_start_str,
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
                    start_time=parse_datetime(start_str),
                    end_time=parse_datetime(end_str),
                    enroll_start=parse_datetime(enroll_start_str),
                    enroll_end=parse_datetime(enroll_end_str),
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
                "id": generate_course_id("课程D", start_str, enroll_start_str, "赵老师", "学院路主M201", "学院路校区"),
                "legacy_id": legacy_id,
                "name": "课程D",
                "teacher": "赵老师",
                "location": "学院路主M201",
                "campus": "学院路校区",
                "category": "博雅课程-德育",
                "start_time": start_str,
                "end_time": end_str,
                "enroll_start": enroll_start_str,
                "enroll_end": enroll_end_str,
                "capacity": 120,
                "enrolled": 119,
                "status": "预告",
            },
            {
                "id": generate_course_id("课程D", start_str, enroll_start_str, "赵老师", "沙河SH3-101", "沙河校区"),
                "legacy_id": legacy_id,
                "name": "课程D",
                "teacher": "赵老师",
                "location": "沙河SH3-101",
                "campus": "沙河校区",
                "category": "博雅课程-德育",
                "start_time": start_str,
                "end_time": end_str,
                "enroll_start": enroll_start_str,
                "enroll_end": enroll_end_str,
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
        now = business_now()
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

    def test_extract_courses_from_network_payload_normalizes_course_rows(self):
        payload = {
            "data": {
                "rows": [
                    {
                        "courseName": "课程网络A",
                        "courseType": "博雅课程-德育",
                        "teacherName": "郭老师",
                        "location": "学院路主M201",
                        "campusName": "全部校区",
                        "startTime": "2026-03-21 15:00",
                        "endTime": "2026-03-21 16:00",
                        "enrollStart": "2026-03-19 12:00",
                        "enrollEnd": "2026-03-21 15:00",
                        "status": "预告",
                        "selectedNum": 0,
                        "maxNum": 200,
                    }
                ]
            }
        }

        rows = _extract_courses_from_network_payload(payload)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "课程网络A")
        self.assertEqual(rows[0]["capacity"], 200)
        self.assertEqual(rows[0]["enrolled"], 0)
        self.assertEqual(rows[0]["campus"], "全部校区")

    def test_assess_scrape_health_blocks_suspiciously_sparse_snapshot(self):
        session = self.Session()
        now = business_now()
        try:
            for index in range(20):
                session.add(
                    Course(
                        id=f"healthy-{index}",
                        name=f"课程{index}",
                        campus="全部校区",
                        enroll_start=now + timedelta(hours=1),
                        enroll_end=now + timedelta(days=1),
                        capacity=100,
                        enrolled=0,
                        expired=False,
                    )
                )
            session.commit()
        finally:
            session.close()

        sparse_rows = [
            {
                "id": "sparse-1",
                "name": "课程预告A",
                "campus": "全部校区",
                "start_time": "2026-03-28 14:30",
                "enroll_start": "2026-03-23 18:00",
                "enroll_end": "2026-03-28 12:00",
                "capacity": 250,
                "enrolled": 0,
                "status": "预告",
            },
            {
                "id": "sparse-2",
                "name": "课程预告B",
                "campus": "全部校区",
                "start_time": "2026-03-29 14:30",
                "enroll_start": "2026-03-24 18:00",
                "enroll_end": "2026-03-29 12:00",
                "capacity": 200,
                "enrolled": 0,
                "status": "预告",
            },
        ]

        with patch("src.scraper.get_session", side_effect=lambda: self.Session()):
            health = assess_scrape_health(sparse_rows)

        self.assertFalse(health["healthy"])
        self.assertEqual(health["db_active_count"], 20)
        self.assertEqual(health["scraped_count"], 2)

    def test_select_best_header_row_prefers_real_course_header_over_filter_header(self):
        header_rows = [
            ["状态", "课程名称", "课程类别", "课程信息", "课程时间", "开放群体", "选课时间", "课程作业", "课程人数", "操作"],
            ["", "检索名称", "检索类别", "检索信息", "全局检索"],
        ]

        headers = _select_best_header_row(header_rows)

        self.assertEqual(headers[0], "状态")
        self.assertIn("课程时间", headers)
        self.assertIn("选课时间", headers)

    def test_sync_course_lifecycle_marks_expired_instead_of_deleting(self):
        now = business_now()
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

    def test_sync_course_lifecycle_marks_finished_course_expired(self):
        now = business_now()
        session = self.Session()
        session.add(
            Course(
                id="finished-course",
                name="课程已结束",
                teacher="王五",
                location="教室5",
                campus="学院路",
                end_time=now - timedelta(minutes=5),
                enroll_end=now + timedelta(hours=1),
                capacity=60,
                enrolled=10,
                expired=False,
            )
        )
        session.commit()
        session.close()

        scheduler._push_buffer["urgent"] = ["finished-course"]
        scheduler._push_buffer["soon"] = ["finished-course"]

        with patch("src.scheduler.get_session", side_effect=lambda: self.Session()):
            scheduler._sync_course_lifecycle()

        verify = self.Session()
        try:
            row = verify.query(Course).filter_by(id="finished-course").first()
            self.assertIsNotNone(row)
            self.assertTrue(row.expired)
            self.assertNotIn("finished-course", scheduler._push_buffer["urgent"])
            self.assertNotIn("finished-course", scheduler._push_buffer["soon"])
        finally:
            verify.close()

    def test_save_courses_to_db_skips_new_course_that_has_already_ended(self):
        ended_row = {
            "id": "ended-row",
            "name": "已经结束的课程",
            "category": "博雅课程-德育",
            "location": "学院路主M201",
            "teacher": "郭老师",
            "college": "学生工作部",
            "start_time": "2026-03-19 09:00",
            "end_time": "2026-03-19 10:00",
            "enroll_start": "2026-03-19 08:00",
            "enroll_end": "2026-03-19 12:00",
            "capacity": 200,
            "enrolled": 20,
            "status": "可选",
            "campus": "全部校区",
        }

        with (
            patch("src.scraper.get_session", side_effect=lambda: self.Session()),
            patch("src.scraper.business_now", return_value=datetime(2026, 3, 19, 12, 30)),
        ):
            new_ids = save_courses_to_db([ended_row])

        self.assertEqual(new_ids, [])
        verify = self.Session()
        try:
            self.assertIsNone(verify.query(Course).filter_by(id="ended-row").first())
        finally:
            verify.close()

    def test_load_active_enrollment_targets_excludes_finished_courses(self):
        now = business_now()
        session = self.Session()
        try:
            session.add(
                Course(
                    id="active-open",
                    name="进行中的课程",
                    campus="全部校区",
                    enroll_start=now - timedelta(minutes=10),
                    enroll_end=now + timedelta(hours=1),
                    end_time=now + timedelta(minutes=30),
                    capacity=200,
                    enrolled=183,
                    expired=False,
                )
            )
            session.add(
                Course(
                    id="active-finished",
                    name="已经下课的课程",
                    campus="全部校区",
                    enroll_start=now - timedelta(minutes=10),
                    enroll_end=now + timedelta(hours=1),
                    end_time=now - timedelta(minutes=1),
                    capacity=200,
                    enrolled=190,
                    expired=False,
                )
            )
            session.commit()

            rows = scheduler._load_active_enrollment_targets(session)
        finally:
            session.close()

        self.assertEqual([row.id for row in rows], ["active-open"])

    def test_check_course_reminders_uses_one_join_query(self):
        now = business_now()
        session = self.Session()
        session.add(
            Course(
                id="reminder-course",
                name="提醒性能测试课程",
                enroll_start=now + timedelta(hours=2),
                enroll_end=now + timedelta(days=1),
                end_time=now + timedelta(days=2),
                capacity=30,
                enrolled=10,
                expired=False,
            )
        )
        subscriber = EmailSubscriber(
            email="reminder-performance@example.com",
            verified=True,
            active=True,
        )
        session.add(subscriber)
        session.flush()
        session.add(
            CourseReminder(
                subscriber_id=subscriber.id,
                course_id="reminder-course",
                remind_before_minutes=5,
                sent=False,
            )
        )
        session.commit()

        statement_count = 0

        def count_statements(*_args):
            nonlocal statement_count
            statement_count += 1

        event.listen(self.engine, "before_cursor_execute", count_statements)
        try:
            with (
                patch("src.scheduler.get_session", return_value=session),
                patch(
                    "src.scheduler.load_filter_config",
                    return_value=FilterConfig(
                        id=1,
                        email_enabled=False,
                        telegram_enabled=False,
                    ),
                ),
            ):
                asyncio.run(scheduler.check_course_reminders())
        finally:
            event.remove(self.engine, "before_cursor_execute", count_statements)

        self.assertEqual(1, statement_count)

    def test_load_hot_watch_targets_detects_nearly_full_course(self):
        now = business_now()
        session = self.Session()
        try:
            session.add(
                Course(
                    id="hot-course",
                    name="热点课",
                    campus="全部校区",
                    enroll_start=now - timedelta(minutes=20),
                    enroll_end=now + timedelta(hours=2),
                    end_time=now + timedelta(hours=3),
                    capacity=200,
                    enrolled=183,
                    expired=False,
                    last_seen=now - timedelta(seconds=40),
                )
            )
            session.add(
                Course(
                    id="cool-course",
                    name="普通课",
                    campus="全部校区",
                    enroll_start=now - timedelta(minutes=20),
                    enroll_end=now + timedelta(hours=2),
                    end_time=now + timedelta(hours=3),
                    capacity=200,
                    enrolled=80,
                    expired=False,
                    last_seen=now - timedelta(seconds=40),
                )
            )
            session.commit()

            hot_rows = scheduler._load_hot_watch_targets(session)
        finally:
            session.close()

        self.assertEqual([row.id for row in hot_rows], ["hot-course"])

    def test_course_to_dict_exposes_hot_watch_metadata(self):
        now = business_now()
        course = Course(
            id="hot-json",
            name="热点展示课",
            campus="全部校区",
            enroll_start=now - timedelta(minutes=10),
            enroll_end=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            capacity=200,
            enrolled=183,
            expired=False,
            last_seen=now - timedelta(seconds=18),
        )

        payload = course.to_dict()

        self.assertTrue(payload["is_hot_course"])
        self.assertEqual(payload["hot_reason"], "fill_ratio")
        self.assertTrue(payload["enrollment_open"])
        self.assertGreater(payload["fill_percent"], 90)
        self.assertEqual(payload["display_check_in_method"], "常规签到")
        self.assertFalse(payload["is_self_check_in"])

    def test_check_in_display_label_never_falls_back_to_direct_enroll_mode(self):
        course = Course(
            id="check-in-label",
            name="签到标签展示课",
            sign_method="直接选课",
            check_in_method="",
        )

        self.assertEqual(get_check_in_display_label(course), "常规签到")
        self.assertFalse(is_self_check_in(course))

        course.check_in_method = "自主签到"
        self.assertEqual(get_check_in_display_label(course), "自主签到")
        self.assertTrue(is_self_check_in(course))

    def test_should_defer_browser_recycle_when_hot_courses_exist(self):
        fake_session = type("FakeSession", (), {"close": lambda self: None})()
        with patch.object(scheduler, "get_session", return_value=fake_session), \
             patch.object(scheduler, "_load_hot_watch_targets", return_value=[object()]):
            self.assertTrue(scheduler._should_defer_browser_recycle(scheduler.BROWSER_MAX_SCRAPE_RUNS))

        with patch.object(scheduler, "get_session", return_value=fake_session), \
             patch.object(scheduler, "_load_hot_watch_targets", return_value=[]):
            self.assertFalse(scheduler._should_defer_browser_recycle(scheduler.BROWSER_MAX_SCRAPE_RUNS))


class ScraperAsyncRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        scheduler._active_scrape_task = None
        scheduler._active_scrape_task_lock = None
        scheduler.run_status["is_running"] = False
        scheduler.run_status["last_error"] = None
        scheduler.run_status["last_scrape_status"] = None

    async def asyncTearDown(self):
        task = scheduler._active_scrape_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass
        scheduler._active_scrape_task = None
        scheduler._active_scrape_task_lock = None
        scheduler.run_status["is_running"] = False
        scheduler.run_status["last_error"] = None
        scheduler.run_status["last_scrape_status"] = None

    async def test_structured_scrape_failure_is_not_treated_as_empty_snapshot(self):
        outcome = ScrapeOutcome(
            status=ScrapeStatus.AUTH_EXPIRED,
            message="课程系统登录状态已失效",
        )

        with (
            patch.object(scheduler, "_ensure_browser", new=AsyncMock(return_value=object())),
            patch.object(scheduler, "_sync_course_lifecycle"),
            patch.object(scheduler, "scrape_courses_result", new=AsyncMock(return_value=outcome)),
            patch.object(scheduler, "close_browser", new=AsyncMock()) as close_browser_mock,
            patch.object(scheduler, "_check_and_alert_failures", new=AsyncMock()),
            patch.object(scheduler, "save_courses_to_db") as save_courses_mock,
        ):
            result = await scheduler._run_scrape_task_impl(mode="quick")

        self.assertFalse(result["success"])
        self.assertEqual(result["scrape_status"], ScrapeStatus.AUTH_EXPIRED.value)
        self.assertEqual(scheduler.run_status["last_scrape_status"], ScrapeStatus.AUTH_EXPIRED.value)
        save_courses_mock.assert_not_called()
        self.assertEqual(close_browser_mock.await_count, 2)

    async def test_telegram_push_is_persisted_before_the_delivery_handler_runs(self):
        now = business_now()
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()
        course = Course(
            id="queued-telegram-course",
            name="排队测试课程",
            enroll_start=now - timedelta(minutes=1),
            enroll_end=now + timedelta(hours=1),
            end_time=now + timedelta(hours=2),
            capacity=20,
            enrolled=5,
            expired=False,
        )
        session.add(course)
        session.commit()

        config = types.SimpleNamespace(email_enabled=False, telegram_enabled=True)
        delivery_summary = {"claimed": 1, "succeeded": 1, "failed": 0, "delivered_count": 1}
        try:
            with (
                patch.object(scheduler, "drain_notification_jobs", new=AsyncMock(return_value=delivery_summary)),
                patch.object(scheduler, "_log_push"),
            ):
                pushed = await scheduler._do_push([course], config, session)

            jobs = session.query(NotificationJob).all()
            self.assertEqual(pushed, 1)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].channel, "telegram")
            self.assertEqual(jobs[0].course_ids, [course.id])
            self.assertEqual(jobs[0].status, "pending")
        finally:
            session.close()
            engine.dispose()

    async def test_trigger_scrape_task_returns_immediately_when_scrape_is_already_running(self):
        async def _long_running():
            await asyncio.sleep(60)

        task = asyncio.create_task(_long_running())
        setattr(task, "_boya_mode", "full")
        scheduler._active_scrape_task = task
        scheduler._active_scrape_task_lock = asyncio.Lock()

        payload = await scheduler.trigger_scrape_task(mode="quick")

        self.assertTrue(payload["success"])
        self.assertFalse(payload["started"])
        self.assertTrue(payload["joined_existing"])
        self.assertTrue(payload["skipped_due_to_active"])

    async def test_run_scrape_task_times_out_and_closes_browser(self):
        async def _slow_impl(mode="full"):
            await asyncio.sleep(0.05)

        previous_timeout = scheduler.SCRAPE_TASK_TIMEOUT_SECONDS
        scheduler.SCRAPE_TASK_TIMEOUT_SECONDS = 0
        try:
            with (
                patch.object(scheduler, "_run_scrape_task_impl", new=AsyncMock(side_effect=_slow_impl)),
                patch.object(scheduler, "close_browser", new=AsyncMock()) as close_browser_mock,
                patch.object(scheduler, "_check_and_alert_failures", new=AsyncMock()) as alert_mock,
            ):
                result = await scheduler.run_scrape_task(mode="quick", join_existing=False)
        finally:
            scheduler.SCRAPE_TASK_TIMEOUT_SECONDS = previous_timeout

        self.assertFalse(result["success"])
        self.assertTrue(result["timed_out"])
        self.assertIn("timed out", result["message"])
        close_browser_mock.assert_awaited()
        alert_mock.assert_awaited()
        self.assertIn("timed out", scheduler.run_status["last_error"])

    def test_build_course_row_payload_handles_preview_row(self):
        headers = ["状态", "课程名称", "课程类别", "检索信息", "课程时间", "全局检索", "选课信息", "作业", "人数", "操作"]
        column_map = {}
        for idx, text in enumerate(headers):
            from src.scraper import _match_column_key
            key = _match_column_key(text)
            if key and key not in column_map:
                column_map[key] = idx

        row = [
            "预告",
            "“承雷锋志，启支教行”——蓝协支教系列项目分享会",
            "博雅课程-劳动教育",
            "地点：学院路校区主M202\n教师：许天宇\n学院：校团委",
            "开始：2026-03-28 14:30\n结束：2026-03-28 15:45",
            "校区：全部校区\n学院：全部学院\n年级：全部年级\n人群：全部人群",
            "选课方式：直接选课\n选课开始：2026-03-23 18:00\n选课结束：2026-03-28 12:00\n退选截止：2026-03-28 12:00",
            "无作业",
            "0/250",
            "详细介绍",
        ]

        payload = _build_course_row_payload(row, column_map, row_index=0, table_index=0)

        self.assertIsNotNone(payload)
        self.assertEqual(payload["status"], "预告")
        self.assertEqual(payload["capacity"], 250)
        self.assertEqual(payload["enrolled"], 0)
        self.assertEqual(payload["campus"], "全部校区")
        self.assertEqual(payload["enroll_start"], "2026-03-23 18:00")

    async def test_collect_current_view_courses_stops_after_empty_page_retry(self):
        page = types.SimpleNamespace(wait_for_timeout=AsyncMock())

        with (
            patch("src.scraper._ensure_session_with_retry", new=AsyncMock(return_value=True)),
            patch("src.scraper._parse_visible_course_tables", new=AsyncMock(side_effect=[[], []])),
            patch("src.scraper._enrich_with_details", new=AsyncMock()),
            patch("src.scraper._go_to_next_page", new=AsyncMock(return_value=True)) as next_page_mock,
        ):
            courses = await _collect_current_view_courses(page, include_details=False, view_name="default")

        self.assertEqual(courses, [])
        self.assertEqual(next_page_mock.await_count, 0)

    async def test_course_select_empty_page_is_a_valid_ready_state(self):
        class EmptyLocator:
            async def count(self):
                return 0

            def nth(self, index):
                return self

            async def is_visible(self):
                return False

        page = types.SimpleNamespace(
            url="https://d.buaa.edu.cn/https/example/system/course-select",
            locator=lambda selector: EmptyLocator(),
            inner_text=AsyncMock(return_value="选择课程 当前暂无可选课程"),
            wait_for_timeout=AsyncMock(),
        )

        self.assertTrue(await _course_page_has_empty_state(page))
        self.assertTrue(await _wait_course_tables_ready(page))

    async def test_empty_marker_on_non_course_page_is_not_treated_as_ready(self):
        page = types.SimpleNamespace(
            url="https://d.buaa.edu.cn/https/example/system/home",
            locator=lambda selector: types.SimpleNamespace(count=AsyncMock(return_value=0)),
            inner_text=AsyncMock(return_value="当前暂无可选课程"),
            wait_for_timeout=AsyncMock(),
        )

        self.assertFalse(await _course_page_has_empty_state(page))

    async def test_parse_visible_course_tables_prefers_dom_fast_path(self):
        page = types.SimpleNamespace()
        class FakeRowLocator:
            def __init__(self, count):
                self._count = count

            async def count(self):
                return self._count

        class FakeTable:
            def __init__(self, row_count):
                self._row_count = row_count

            def locator(self, selector):
                assert selector == "tbody tr"
                return FakeRowLocator(self._row_count)

        dom_rows = []
        for index in range(5):
            preview_id = generate_course_id(
                f"课程预告A-{index}",
                f"2026-03-28 1{index}:30",
                "2026-03-23 18:00",
                "许天宇",
                "学院路校区主M202",
                "全部校区",
            )
            dom_rows.append(
                {
                    "id": preview_id,
                    "name": f"课程预告A-{index}",
                    "category": "博雅课程-劳动教育",
                    "location": "学院路校区主M202",
                    "teacher": "许天宇",
                    "start_time": f"2026-03-28 1{index}:30",
                    "end_time": f"2026-03-28 1{index}:45",
                    "enroll_start": "2026-03-23 18:00",
                    "enroll_end": "2026-03-28 12:00",
                    "capacity": 250,
                    "enrolled": 0,
                    "status": "预告",
                    "__row_index": index,
                    "__table_index": 0,
                }
            )

        with (
            patch(
                "src.scraper._extract_visible_course_rows_via_dom",
                new=AsyncMock(return_value=dom_rows)),
            patch(
                "src.scraper._get_visible_course_tables",
                new=AsyncMock(return_value=[(0, FakeTable(5))]),
            ) as visible_tables_mock,
        ):
            rows = await _parse_visible_course_tables(page)

        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]["capacity"], 250)
        self.assertEqual(visible_tables_mock.await_count, 1)

    async def test_parse_visible_course_tables_runs_fallback_when_fast_path_is_sparse(self):
        page = types.SimpleNamespace()
        class FakeRowLocator:
            def __init__(self, count):
                self._count = count

            async def count(self):
                return self._count

        class FakeTable:
            def __init__(self, row_count):
                self._row_count = row_count

            def locator(self, selector):
                assert selector == "tbody tr"
                return FakeRowLocator(self._row_count)

        dom_row = {
            "id": "dom-only",
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
            "__row_index": 0,
            "__table_index": 0,
        }
        locator_row = dict(dom_row)
        locator_row["id"] = "locator-only"
        locator_row["name"] = "课程预告B"

        with (
            patch("src.scraper._extract_visible_course_rows_via_dom", new=AsyncMock(return_value=[dom_row])),
            patch("src.scraper._get_visible_course_tables", new=AsyncMock(return_value=[(0, FakeTable(5))])),
            patch("src.scraper._parse_course_table", new=AsyncMock(return_value=[locator_row])) as parse_table_mock,
        ):
            rows = await _parse_visible_course_tables(page)

        self.assertEqual({row["id"] for row in rows}, {"dom-only", "locator-only"})
        self.assertEqual(parse_table_mock.await_count, 1)


if __name__ == "__main__":
    unittest.main()
