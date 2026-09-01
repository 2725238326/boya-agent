import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import src.enroll as enroll


class AutoEnrollSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_failure_circuit_stops_follow_up_attempts(self):
        config = SimpleNamespace(
            auto_enroll_enabled=True,
            max_auto_enroll_per_day=5,
            confirm_before_enroll=False,
            telegram_enabled=False,
            email_enabled=False,
        )
        courses = [
            SimpleNamespace(id="course-1", name="课程一"),
            SimpleNamespace(id="course-2", name="课程二"),
        ]
        attempt = AsyncMock(return_value=(False, "选课失败"))

        with (
            patch.object(enroll, "load_filter_config", return_value=config),
            patch.object(enroll, "get_today_enroll_count", return_value=0),
            patch.object(enroll, "get_today_enroll_failure_count", return_value=2),
            patch.object(enroll, "get_auto_enroll_candidates", return_value=courses),
            patch.object(enroll, "attempt_enroll", new=attempt),
            patch.object(enroll, "log_enroll_attempt"),
            patch.object(enroll, "AUTO_ENROLL_FAILURE_LIMIT", 3),
        ):
            await enroll.auto_enroll_if_enabled(object(), courses)

        self.assertEqual(1, attempt.await_count)


if __name__ == "__main__":
    unittest.main()
