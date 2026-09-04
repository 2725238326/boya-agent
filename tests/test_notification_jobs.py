import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Course, EmailSubscriber, NotificationEvent, NotificationJob
from src.notification_jobs import (
    NotificationDeliveryResult,
    NotificationJobStatus,
    claim_next_notification_job,
    drain_notification_jobs,
    enqueue_notification_job,
    mark_notification_job_failure,
)
from src.time_utils import now as business_now


class NotificationJobRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def _session_factory(self):
        return self.Session()

    def test_enqueue_is_idempotent_and_does_not_put_email_in_key(self):
        session = self.Session()
        try:
            first = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                subscriber_email="person@example.com",
                course_ids=["course-2", "course-1"],
                event_type="new",
                delivery_mode="priority",
                dedupe_material="course-1:2;course-2:5",
            )
            second = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                subscriber_email="person@example.com",
                course_ids=["course-2", "course-1"],
                event_type="new",
                delivery_mode="priority",
                dedupe_material="course-1:2;course-2:5",
            )
            session.commit()

            self.assertEqual(first.id, second.id)
            self.assertNotIn("person@example.com", first.idempotency_key)
            self.assertEqual(first.course_ids, ["course-2", "course-1"])
            self.assertEqual(session.query(type(first)).count(), 1)
        finally:
            session.close()

    async def test_drain_claims_a_persisted_job_and_marks_it_successful(self):
        session = self.Session()
        try:
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                subscriber_email="person@example.com",
                course_ids=["course-1"],
            )
            session.commit()
            job_id = job.id
        finally:
            session.close()

        handled = []

        async def handler(claimed_job):
            handled.append((claimed_job.id, claimed_job.attempts))
            return NotificationDeliveryResult(True, delivered_count=1, message="ok")

        with patch("src.notification_jobs.get_session", side_effect=self._session_factory):
            result = await drain_notification_jobs({"email": handler}, limit=1, worker_id="test-worker")

        verify = self.Session()
        try:
            stored = verify.get(type(job), job_id)
            self.assertEqual(handled, [(job_id, 1)])
            self.assertEqual(result["claimed"], 1)
            self.assertEqual(result["succeeded"], 1)
            self.assertEqual(result["delivered_count"], 1)
            self.assertEqual(stored.status, NotificationJobStatus.SUCCEEDED)
            self.assertEqual(stored.attempts, 1)
            self.assertIsNotNone(stored.completed_at)
        finally:
            verify.close()

    def test_failed_job_retries_with_backoff_and_stops_at_attempt_limit(self):
        session = self.Session()
        try:
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                max_attempts=2,
            )
            session.commit()

            first_claim = claim_next_notification_job(session, channels=["email"], worker_id="test")
            self.assertIsNotNone(first_claim)
            mark_notification_job_failure(session, first_claim.id, "temporary failure", retry_base_seconds=0)

            second_claim = claim_next_notification_job(session, channels=["email"], worker_id="test")
            self.assertIsNotNone(second_claim)
            mark_notification_job_failure(session, second_claim.id, "permanent failure", retry_base_seconds=0)

            self.assertIsNone(claim_next_notification_job(session, channels=["email"], worker_id="test"))
            stored = session.get(type(job), job.id)
            self.assertEqual(stored.status, NotificationJobStatus.FAILED)
            self.assertEqual(stored.attempts, 2)
            self.assertIsNone(stored.available_at)
            self.assertEqual(stored.last_error, "permanent failure")
        finally:
            session.close()

    def test_terminal_failure_can_start_a_new_bounded_attempt_cycle(self):
        session = self.Session()
        try:
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                max_attempts=1,
            )
            session.commit()

            claimed = claim_next_notification_job(session, channels=["email"], worker_id="test")
            mark_notification_job_failure(session, claimed.id, "temporary failure", retry_base_seconds=0)
            failed_job = session.get(NotificationJob, job.id)
            self.assertEqual(failed_job.status, NotificationJobStatus.FAILED)

            requeued = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                max_attempts=1,
            )
            session.commit()

            self.assertEqual(requeued.id, job.id)
            self.assertEqual(requeued.status, NotificationJobStatus.PENDING)
            self.assertEqual(requeued.attempts, 0)
            self.assertEqual(requeued.last_error, "")
        finally:
            session.close()

    def test_stale_processing_lease_at_max_attempts_becomes_terminal_failure(self):
        session = self.Session()
        try:
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                max_attempts=1,
            )
            session.commit()

            claimed = claim_next_notification_job(session, channels=["email"], worker_id="test")
            stored = session.get(NotificationJob, claimed.id)
            stored.locked_at = business_now() - timedelta(minutes=10)
            session.commit()

            self.assertIsNone(claim_next_notification_job(session, channels=["email"], worker_id="recovery"))
            verify = self.Session()
            try:
                stored = verify.get(NotificationJob, job.id)
                self.assertEqual(stored.status, NotificationJobStatus.FAILED)
                self.assertIsNone(stored.available_at)
                self.assertIn("lease expired", stored.last_error)
            finally:
                verify.close()
        finally:
            session.close()


class EmailNotificationJobIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def test_job_schema_is_created_with_the_application_models(self):
        table_names = set(self.engine.dialect.get_table_names(self.engine.connect()))
        self.assertIn("notification_jobs", table_names)

    async def test_email_push_persists_job_before_delivery_and_records_event(self):
        from src.push import email_push

        now = business_now()
        session = self.Session()
        try:
            course = Course(
                id="course-1",
                name="测试课程",
                category="艺术",
                teacher="测试老师",
                campus="学院路校区",
                start_time=now + timedelta(days=1),
                end_time=now + timedelta(days=1, hours=1),
                enroll_start=now - timedelta(minutes=5),
                enroll_end=now + timedelta(hours=2),
                capacity=30,
                enrolled=10,
                expired=False,
            )
            subscriber = EmailSubscriber(
                email="person@example.com",
                verified=True,
                active=True,
                self_sign_only=False,
            )
            session.add_all([course, subscriber])
            session.commit()
        finally:
            session.close()

        with (
            patch("src.models.get_session", side_effect=lambda: self.Session()),
            patch("src.notification_jobs.get_session", side_effect=lambda: self.Session()),
            patch.object(email_push, "_send_raw_email", return_value=True) as send_mock,
        ):
            sent = await email_push.send_email_to_subscribers(
                [course],
                base_url="https://buaaboya.top",
                event_type="new",
                delivery_mode="priority",
            )

        verify = self.Session()
        try:
            self.assertEqual(sent, 1)
            self.assertEqual(send_mock.call_count, 1)
            jobs = verify.query(NotificationJob).all()
            events = verify.query(NotificationEvent).all()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].status, NotificationJobStatus.SUCCEEDED)
            self.assertEqual(len(events), 1)
            self.assertTrue(events[0].success)
            self.assertEqual(events[0].course_id, "course-1")
        finally:
            verify.close()


if __name__ == "__main__":
    unittest.main()
