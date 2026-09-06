import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import scraper
from src.scrape_outcome import (
    ScrapeNavigationError,
    ScrapePageNotReadyError,
    ScrapeStatus,
)


class ScrapeOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_page_is_a_successful_empty_result(self):
        page = SimpleNamespace(url=scraper.BYKC_COURSE_URL)

        with patch.object(scraper, "_scrape_courses_impl", new=AsyncMock(return_value=[])):
            outcome = await scraper.scrape_courses_result(page)

        self.assertEqual(outcome.status, ScrapeStatus.SUCCESS_EMPTY)
        self.assertTrue(outcome.success)
        self.assertTrue(outcome.is_empty)
        self.assertEqual(outcome.courses, [])
        self.assertEqual(outcome.to_dict()["scraped_count"], 0)
        self.assertGreaterEqual(outcome.metadata["duration_ms"], 0)
        self.assertTrue(outcome.metadata["include_details"])

    async def test_courses_are_returned_as_a_successful_snapshot(self):
        courses = [{"id": "course-1", "name": "测试课程"}]
        page = SimpleNamespace(url=scraper.BYKC_COURSE_URL)

        with patch.object(scraper, "_scrape_courses_impl", new=AsyncMock(return_value=courses)):
            outcome = await scraper.scrape_courses_result(page)

        self.assertEqual(outcome.status, ScrapeStatus.SUCCESS_WITH_COURSES)
        self.assertTrue(outcome.success)
        self.assertFalse(outcome.is_empty)
        self.assertEqual(outcome.courses, courses)
        self.assertEqual(outcome.to_dict()["scraped_count"], 1)
        self.assertGreaterEqual(outcome.metadata["duration_ms"], 0)

    async def test_sso_page_is_classified_as_auth_expired(self):
        page = SimpleNamespace(url="https://sso.buaa.edu.cn/login")

        with patch.object(
            scraper,
            "_scrape_courses_impl",
            new=AsyncMock(side_effect=RuntimeError("page redirected")),
        ):
            outcome = await scraper.scrape_courses_result(page)

        self.assertEqual(outcome.status, ScrapeStatus.AUTH_EXPIRED)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.metadata["exception_type"], "RuntimeError")

    async def test_navigation_failure_is_not_reported_as_empty(self):
        page = SimpleNamespace(url=scraper.BYKC_COURSE_URL)

        with patch.object(
            scraper,
            "_scrape_courses_impl",
            new=AsyncMock(side_effect=ScrapeNavigationError("navigation failed")),
        ):
            outcome = await scraper.scrape_courses_result(page)

        self.assertEqual(outcome.status, ScrapeStatus.UPSTREAM_UNAVAILABLE)
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.to_dict()["scraped_count"], 0)

    async def test_page_timeout_is_classified_as_timeout(self):
        page = SimpleNamespace(url=scraper.BYKC_COURSE_URL)

        with patch.object(
            scraper,
            "_scrape_courses_impl",
            new=AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            outcome = await scraper.scrape_courses_result(page)

        self.assertEqual(outcome.status, ScrapeStatus.TIMEOUT)
        self.assertFalse(outcome.success)

    async def test_page_not_ready_is_classified_as_parse_failure(self):
        page = SimpleNamespace(url=scraper.BYKC_COURSE_URL)

        with patch.object(
            scraper,
            "_scrape_courses_impl",
            new=AsyncMock(side_effect=ScrapePageNotReadyError("table missing")),
        ):
            outcome = await scraper.scrape_courses_result(page)

        self.assertEqual(outcome.status, ScrapeStatus.PARSE_FAILED)
        self.assertFalse(outcome.success)

    async def test_legacy_list_api_keeps_its_return_shape_on_failure(self):
        page = SimpleNamespace(url=scraper.BYKC_COURSE_URL)

        with patch.object(
            scraper,
            "_scrape_courses_impl",
            new=AsyncMock(side_effect=ScrapeNavigationError("navigation failed")),
        ):
            courses = await scraper.scrape_courses(page)

        self.assertEqual(courses, [])


if __name__ == "__main__":
    unittest.main()
