import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.course_state import (
    get_check_in_display_label,
    get_hot_reason,
    is_course_expired,
    is_enrollment_open,
    is_hot_course,
    is_self_check_in,
)


class CourseStateTests(unittest.TestCase):
    NOW = datetime(2026, 9, 2, 12, 0, 0)

    def _course(self, **overrides):
        values = {
            "check_in_method": "常规签到",
            "sign_method": "直接选课",
            "enroll_start": self.NOW - timedelta(hours=1),
            "enroll_end": self.NOW + timedelta(days=1),
            "end_time": self.NOW + timedelta(days=2),
            "expired": False,
            "capacity": 100,
            "enrolled": 50,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_check_in_label_has_one_normalized_rule(self):
        self.assertEqual("自主签到", get_check_in_display_label(self._course(check_in_method="扫码自主签到")))
        self.assertTrue(is_self_check_in(self._course(sign_method="自选课程")))
        self.assertEqual("常规签到", get_check_in_display_label(self._course()))

    def test_enrollment_window_and_expiry_boundaries(self):
        self.assertFalse(is_enrollment_open(
            self._course(enroll_start=self.NOW + timedelta(minutes=1)), self.NOW
        ))
        self.assertTrue(is_enrollment_open(
            self._course(enroll_start=self.NOW, enroll_end=self.NOW + timedelta(hours=1)), self.NOW
        ))
        self.assertFalse(is_enrollment_open(
            self._course(enroll_end=self.NOW), self.NOW
        ))
        self.assertTrue(is_course_expired(
            self._course(end_time=self.NOW), self.NOW
        ))

    def test_hot_course_uses_remaining_or_fill_ratio_and_never_expired(self):
        low_remaining = self._course(capacity=100, enrolled=98)
        self.assertTrue(is_hot_course(low_remaining, self.NOW, remaining_threshold=3, fill_ratio_threshold=0.95))
        self.assertEqual("remaining", get_hot_reason(low_remaining, self.NOW, remaining_threshold=3, fill_ratio_threshold=0.99))

        high_fill = self._course(capacity=100, enrolled=90)
        self.assertEqual("fill_ratio", get_hot_reason(high_fill, self.NOW, remaining_threshold=3, fill_ratio_threshold=0.9))
        self.assertFalse(is_hot_course(self._course(expired=True), self.NOW))


if __name__ == "__main__":
    unittest.main()
