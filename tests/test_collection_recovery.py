"""Keep recoverable scrape faults separate from browser and authentication loss."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import scheduler
from src.collection.pagination import course_page_identity
from src.collection.recovery import browser_disconnected, requires_browser_reset
from src.collection.timing import attempt_timings, measure_stage
from src.scrape_outcome import ScrapeOutcome, ScrapeStatus
from src.scraper import _select_detail_enrich_targets
from src.time_utils import now as business_now


class CollectionPolicyTests(unittest.TestCase):
    def test_seat_and_status_changes_do_not_change_page_identity(self):
        before = [{"id": "a", "capacity": 20, "enrolled": 20, "status": "已满"}, {"id": "b"}]
        after = [{"id": "b"}, {"id": "a", "capacity": 30, "enrolled": 19, "status": "可报名"}]
        self.assertEqual(course_page_identity(before), course_page_identity(after))
        self.assertNotEqual(course_page_identity(before), course_page_identity([{"id": "c"}]))

    def test_recoverable_failures_do_not_invalidate_session(self):
        for status in (ScrapeStatus.PARSE_FAILED, ScrapeStatus.TIMEOUT, ScrapeStatus.UPSTREAM_UNAVAILABLE):
            with self.subTest(status=status):
                self.assertFalse(requires_browser_reset(status.value))
                self.assertTrue(requires_browser_reset(status.value, {"browser_disconnected": True}))
        self.assertTrue(requires_browser_reset(ScrapeStatus.AUTH_EXPIRED.value))

    def test_browser_health_does_not_depend_only_on_exception_type(self):
        self.assertTrue(browser_disconnected(SimpleNamespace(is_closed=lambda: True), RuntimeError("closed")))
        page = SimpleNamespace(is_closed=lambda: False, context=SimpleNamespace(browser=SimpleNamespace(is_connected=lambda: False)))
        self.assertTrue(browser_disconnected(page, RuntimeError("closed")))
        self.assertFalse(browser_disconnected(SimpleNamespace(), RuntimeError("table missing")))

    def test_finished_history_does_not_consume_new_course_detail_work(self):
        old = (business_now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        rows = [{'id':'history','end_time':old}, {'id':'new'}]
        self.assertEqual(_select_detail_enrich_targets(rows, {'history'}, set()), {'new'})


class CollectionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_retry_reuses_browser_and_never_saves_failed_snapshot(self):
        for disconnected in (False, True):
            with self.subTest(disconnected=disconnected):
                outcomes = [ScrapeOutcome(ScrapeStatus.PARSE_FAILED, metadata={"browser_disconnected": disconnected}),
                            ScrapeOutcome(ScrapeStatus.SUCCESS_EMPTY, metadata={"stages_ms": {"pagination": 20}})]
                with (
                    patch.object(scheduler, "_load_detail_enrich_context", return_value=(set(), set())),
                    patch.object(scheduler, "get_session") as sessions,
                    patch.object(scheduler, "_ensure_browser", new=AsyncMock(return_value=object())) as ensure,
                    patch.object(scheduler, "_sync_course_lifecycle"),
                    patch.object(scheduler, "scrape_courses_result", new=AsyncMock(side_effect=outcomes)),
                    patch.object(scheduler, "close_browser", new=AsyncMock()) as close,
                    patch.object(scheduler, "_note_scrape_success", new=AsyncMock()),
                    patch.object(scheduler, "assess_scrape_health", return_value={"healthy": True}),
                    patch.object(scheduler, "save_courses_to_db") as save,
                ):
                    result = await scheduler._run_scrape_task_impl("quick")
                self.assertTrue(result["success"])
                self.assertEqual([call.kwargs["force_recreate"] for call in ensure.await_args_list], [False, disconnected])
                self.assertEqual(close.await_count, int(disconnected))
                self.assertEqual(scheduler.run_status["last_scrape_attempts"], 2)
                self.assertEqual(scheduler.run_status["last_scrape_stage_ms"], {"pagination": 20})
                save.assert_not_called()

    async def test_failed_round_keeps_session_but_reports_failure(self):
        with (
            patch.object(scheduler, "_load_detail_enrich_context", return_value=(set(), set())),
            patch.object(scheduler, "get_session"),
            patch.object(scheduler, "_ensure_browser", new=AsyncMock(return_value=object())),
            patch.object(scheduler, "_sync_course_lifecycle"),
            patch.object(scheduler, "scrape_courses_result", new=AsyncMock(return_value=ScrapeOutcome(ScrapeStatus.PARSE_FAILED))),
            patch.object(scheduler, "close_browser", new=AsyncMock()) as close,
            patch.object(scheduler, "_check_and_alert_failures", new=AsyncMock()),
            patch.object(scheduler, "save_courses_to_db") as save,
        ):
            result = await scheduler._run_scrape_task_impl("quick")
        self.assertFalse(result["success"])
        close.assert_not_awaited()
        save.assert_not_called()

    async def test_playwright_stops_even_when_browser_close_fails(self):
        browser = SimpleNamespace(close=AsyncMock(side_effect=RuntimeError("close failed")))
        driver = SimpleNamespace(stop=AsyncMock())
        await scheduler._close_browser_local(driver, browser)
        driver.stop.assert_awaited_once()

    async def test_suspicious_empty_retries_without_clearing_data_or_logging_in(self):
        for recovered in (True, False):
            with (
                patch.object(scheduler, "_load_detail_enrich_context", return_value=(set(), set())),
                patch.object(scheduler, "get_session"),
                patch.object(scheduler, "_ensure_browser", new=AsyncMock(return_value=object())) as ensure,
                patch.object(scheduler, "_sync_course_lifecycle"),
                patch.object(scheduler, "scrape_courses_result", new=AsyncMock(return_value=ScrapeOutcome(ScrapeStatus.SUCCESS_EMPTY))),
                patch.object(scheduler, "assess_scrape_health", side_effect=[
                    {"healthy":False,"db_active_count":12,"minimum_expected":5},
                    {"healthy":recovered,"db_active_count":12,"minimum_expected":5}]),
                patch.object(scheduler, "close_browser", new=AsyncMock()) as close,
                patch.object(scheduler, "_note_scrape_success", new=AsyncMock()),
                patch.object(scheduler, "_check_and_alert_failures", new=AsyncMock()),
                patch.object(scheduler, "save_courses_to_db") as save,
            ):
                result = await scheduler._run_scrape_task_impl("quick")
                self.assertEqual(result["success"], recovered)
                self.assertEqual(ensure.await_count, 2)
                self.assertTrue(all(not call.kwargs['force_recreate'] for call in ensure.await_args_list))
                close.assert_not_awaited()
                save.assert_not_called()
                if not recovered:
                    self.assertEqual(result['scrape_status'], 'parse_failed')

    async def test_stage_measurements_are_isolated_and_survive_failure(self):
        async def attempt(name):
            with attempt_timings() as values:
                with measure_stage(name):
                    await asyncio.sleep(0)
                try:
                    with measure_stage("failure"):
                        raise RuntimeError("fixture")
                except RuntimeError:
                    pass
            return values
        first, second = await asyncio.gather(attempt("parse"), attempt("details"))
        self.assertEqual(set(first), {"parse", "failure"})
        self.assertEqual(set(second), {"details", "failure"})
