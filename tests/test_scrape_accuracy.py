import types
import unittest
from unittest.mock import AsyncMock, patch

from src import scraper
from src.scrape_outcome import ScrapePageNotReadyError, ScrapeNavigationError


class AccuracyTests(unittest.IsolatedAsyncioTestCase):
    def test_latest_capacity_pair_wins_in_both_directions(self):
        for old, new in ((5, 20), (20, 5)):
            with self.subTest(old=old, new=new):
                merged = scraper._dedupe_scraped_courses([
                    {"id": "same", "capacity": 20, "enrolled": old, "check_in_method": "自主签到"},
                    {"id": "same", "capacity": 20, "enrolled": new, "check_in_method": ""}])[0]
                self.assertEqual((merged["capacity"], merged["enrolled"]), (20, new))
                self.assertEqual(merged["check_in_method"], "自主签到")
        merged = scraper._merge_scraped_course(
            {"capacity": 100, "enrolled": 99}, {"capacity": 20, "enrolled": 18})
        self.assertEqual((merged["capacity"], merged["enrolled"]), (20, 18))
        self.assertEqual(scraper._merge_scraped_course(merged, {"capacity": 0, "enrolled": 0}), merged)

    async def test_table_signature_includes_tail_and_all_tables(self):
        tables = [(index, types.SimpleNamespace(inner_text=AsyncMock(return_value="同一前缀" * 100 + str(index))))
                  for index in range(3)]
        with patch.object(scraper, "_get_visible_course_tables", new=AsyncMock(return_value=tables)):
            before = await scraper._capture_visible_course_table_signature(None)
            tables[2][1].inner_text.return_value += "changed"
            after = await scraper._capture_visible_course_table_signature(None)
        self.assertNotEqual(before, after)

    async def test_repeated_pages_and_lost_session_reject_partial_snapshot(self):
        page = types.SimpleNamespace()
        rows = [{"id": "same"}]
        for sessions, error in (([True, True], ScrapePageNotReadyError), ([True, False], ScrapeNavigationError)):
            with self.subTest(sessions=sessions), patch.object(
                scraper, "_ensure_session_with_retry", new=AsyncMock(side_effect=sessions)
            ), patch.object(scraper, "_load_current_view_page_courses", new=AsyncMock(return_value=rows)), patch.object(
                scraper, "_go_to_next_page", new=AsyncMock(return_value=True)
            ):
                with self.assertRaises(error):
                    await scraper._collect_current_view_courses(page, False, "fixture")

    async def test_page_limit_rejects_partial_snapshot(self):
        with patch.object(scraper, "MAX_PAGES_PER_VIEW", 1), patch.object(
            scraper, "_ensure_session_with_retry", new=AsyncMock(return_value=True)
        ), patch.object(scraper, "_load_current_view_page_courses", new=AsyncMock(return_value=[{"id": "1"}])), patch.object(
            scraper, "_go_to_next_page", new=AsyncMock(return_value=True)
        ):
            with self.assertRaises(ScrapePageNotReadyError):
                await scraper._collect_current_view_courses(None, False, "fixture")

    async def test_declared_total_mismatch_rejects_silent_missing_pages(self):
        with patch.object(scraper, "_visible_course_total", new=AsyncMock(return_value=3)), patch.object(
            scraper, "_ensure_session_with_retry", new=AsyncMock(return_value=True)
        ), patch.object(scraper, "_load_current_view_page_courses", new=AsyncMock(return_value=[{"id": "1"}])), patch.object(
            scraper, "_go_to_next_page", new=AsyncMock(return_value=False)
        ):
            with self.assertRaises(ScrapePageNotReadyError):
                await scraper._collect_current_view_courses(None, False, "fixture")
