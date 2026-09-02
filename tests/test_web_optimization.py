import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("WEB_SECRET_KEY", "test-secret-for-web-optimization-0123456789")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

from src.models import Base, Course, CourseReminder  # noqa: E402
import web.app as web_module  # noqa: E402


class WebOptimizationTests(unittest.TestCase):
    def setUp(self):
        web_module.app.config.update(TESTING=True)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_reminders_are_serialized_with_one_join_query(self):
        course = Course(
            id="course-1",
            name="测试课程",
            category="艺术",
            teacher="测试老师",
            enroll_start=web_module.business_now(),
            capacity=20,
            enrolled=10,
            expired=False,
        )
        self.session.add(course)
        self.session.add_all([
            CourseReminder(subscriber_id=7, course_id=course.id, sent=False),
            CourseReminder(subscriber_id=7, course_id="missing-course", sent=False),
        ])
        self.session.commit()

        statement_count = 0

        def count_statements(*_args):
            nonlocal statement_count
            statement_count += 1

        event.listen(self.engine, "before_cursor_execute", count_statements)
        try:
            reminders = web_module._serialize_course_reminders(self.session, 7)
        finally:
            event.remove(self.engine, "before_cursor_execute", count_statements)

        self.assertEqual(1, statement_count)
        self.assertEqual(2, len(reminders))
        reminder_names = {item["course_id"]: item["course_name"] for item in reminders}
        self.assertEqual("测试课程", reminder_names["course-1"])
        self.assertEqual("未知课程", reminder_names["missing-course"])

    def test_static_assets_are_versioned_and_cacheable(self):
        with web_module.app.test_request_context("/"):
            asset_url = web_module.static_asset("home.js")
        self.assertIn("/static/home.js?v=", asset_url)

        response = web_module.app.test_client().get("/static/home.js")
        self.assertEqual(200, response.status_code)
        self.assertIn("max-age=604800", response.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
