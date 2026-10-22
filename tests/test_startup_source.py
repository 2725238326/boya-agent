from datetime import datetime
import unittest
from unittest.mock import Mock

from scripts.check_startup_source import source_state, wait_for_source


class StartupSourceTests(unittest.TestCase):
    def setUp(self):
        self.started = datetime.fromisoformat("2026-10-22T07:42:50+00:00")
        self.ready = {"success": True, "source": {
            "degraded": False, "last_success": "2026-10-22 15:43:50",
        }}

    def run_wait(self, fetch):
        elapsed = [0]

        def sleep(seconds):
            elapsed[0] += seconds

        return wait_for_source("http://fixture.test/api/courses", self.started, "Asia/Shanghai",
                               timeout=3, interval=1, fetch=fetch,
                               clock=lambda: elapsed[0], sleep=sleep)

    def test_cold_start_failure_then_scheduled_recovery_passes(self):
        fetch = Mock(side_effect=[{"success": True, "source": {"degraded": True}}, self.ready])
        self.assertTrue(self.run_wait(fetch))
        self.assertEqual(fetch.call_count, 2)

    def test_continuous_failure_times_out_without_being_accepted(self):
        fetch = Mock(return_value={"success": True, "source": {"degraded": True}})
        self.assertFalse(self.run_wait(fetch))
        self.assertEqual(fetch.call_count, 3)

    def test_success_before_restart_and_unknown_source_are_rejected(self):
        for source in ({"degraded": False, "last_success": "2026-10-21 15:43:50"},
                       {"degraded": False}, {"degraded": False, "last_success": "invalid"},
                       {"last_success": "2026-10-22 15:43:50"}):
            with self.subTest(source=source):
                self.assertNotEqual(source_state({"success": True, "source": source},
                                                 self.started, "Asia/Shanghai"), "ready")

    def test_transient_http_failure_is_retried_within_window(self):
        self.assertTrue(self.run_wait(Mock(side_effect=[OSError("fixture only"), self.ready])))

    def test_aware_timestamp_and_legitimate_empty_catalog_are_accepted(self):
        self.ready.update(data=[], total=0)
        self.ready["source"]["last_success"] = "2026-10-22T07:43:50+00:00"
        self.assertEqual(source_state(self.ready, self.started, "Asia/Shanghai"), "ready")
