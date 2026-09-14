import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Course, CourseReminder, EmailSubscriber, NotificationEvent, NotificationJob
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
        self.email_policy = patch.dict(os.environ, {"EMAIL_DELIVERY_ENABLED": "true"})
        self.email_policy.start()
        self.addCleanup(self.email_policy.stop)
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
            mark_notification_job_failure(session, first_claim, "temporary failure", retry_base_seconds=0)

            second_claim = claim_next_notification_job(session, channels=["email"], worker_id="test")
            self.assertIsNotNone(second_claim)
            mark_notification_job_failure(session, second_claim, "permanent failure", retry_base_seconds=0)

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
            mark_notification_job_failure(session, claimed, "temporary failure", retry_base_seconds=0)
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

    def test_terminal_failure_can_remain_terminal_when_requeue_is_disabled(self):
        session = self.Session()
        try:
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                job_type="course_reminder",
                dedupe_material="reminder_id:10",
                max_attempts=1,
            )
            session.commit()

            claimed = claim_next_notification_job(session, channels=["email"], worker_id="test")
            mark_notification_job_failure(session, claimed, "permanent failure", retry_base_seconds=0)
            unchanged = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                job_type="course_reminder",
                dedupe_material="reminder_id:10",
                max_attempts=1,
                reset_terminal_failure=False,
            )

            self.assertEqual(unchanged.id, job.id)
            self.assertEqual(unchanged.status, NotificationJobStatus.FAILED)
            self.assertEqual(unchanged.attempts, 1)
        finally:
            session.close()

    def test_claim_can_be_limited_to_the_callers_job_types(self):
        session = self.Session()
        try:
            reminder = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
                job_type="course_reminder",
                dedupe_material="reminder_id:1",
            )
            push = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=7,
                course_ids=["course-1"],
            )
            session.commit()

            claimed = claim_next_notification_job(
                session, channels=["email"], job_types=["course_push"], worker_id="test"
            )
            self.assertEqual(claimed.id, push.id)
            self.assertIsNone(
                claim_next_notification_job(session, channels=["email"], job_types=["course_push"], worker_id="test")
            )
            recovered = claim_next_notification_job(session, channels=["email"], worker_id="recovery")
            self.assertEqual(recovered.id, reminder.id)
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

    async def test_late_worker_result_cannot_overwrite_recovered_delivery(self):
        for late_success in (False, True):
            for recovered_success in (False, True):
                with self.subTest(late_success=late_success, recovered_success=recovered_success):
                    with self.Session() as session:
                        job = enqueue_notification_job(
                            session, channel="email",
                            course_ids=[f"race-{late_success}-{recovered_success}"],
                        )
                        session.commit()
                        job_id = job.id

                    async def late_handler(claimed):
                        # The first delivery is still running when recovery takes over.
                        recovered_time = claimed.locked_at + timedelta(seconds=181)
                        with patch("src.notification_jobs.business_now", return_value=recovered_time):
                            recovered = await drain_notification_jobs(
                                {"email": lambda _: NotificationDeliveryResult(
                                    recovered_success, int(recovered_success), "recovered result"
                                )}, limit=1,
                            )
                        self.assertEqual(recovered["claimed"], 1)
                        return NotificationDeliveryResult(late_success, int(late_success), "late result")

                    with patch("src.notification_jobs.get_session", side_effect=self._session_factory):
                        result = await drain_notification_jobs({"email": late_handler}, limit=1)

                    with self.Session() as session:
                        stored = session.get(NotificationJob, job_id)
                        self.assertEqual(stored.last_error, "recovered result")
                        self.assertEqual(stored.attempts, 2)
                        self.assertEqual(stored.status, NotificationJobStatus.SUCCEEDED
                                         if recovered_success else NotificationJobStatus.FAILED)
                        self.assertEqual(stored.completed_at is not None, recovered_success)
                        self.assertEqual(stored.available_at is not None, not recovered_success)
                    self.assertEqual(result["succeeded"], 0)
                    self.assertEqual(result["failed"], 0)
                    self.assertEqual(result["delivered_count"], 0)

    async def test_late_failure_cannot_clear_an_active_recovery_lease(self):
        with self.Session() as session:
            job = enqueue_notification_job(session, channel="email", course_ids=["active-race"])
            session.commit()
            job_id = job.id

        async def late_handler(claimed):
            recovered_time = claimed.locked_at + timedelta(seconds=181)
            with patch("src.notification_jobs.business_now", return_value=recovered_time):
                with self.Session() as session:
                    recovered = claim_next_notification_job(session, channels=["email"])
                    self.assertEqual(recovered.id, job_id)
            return NotificationDeliveryResult(False, message="late failure")

        with patch("src.notification_jobs.get_session", side_effect=self._session_factory):
            result = await drain_notification_jobs({"email": late_handler}, limit=1)

        with self.Session() as session:
            stored = session.get(NotificationJob, job_id)
            self.assertEqual(stored.status, NotificationJobStatus.PROCESSING)
            self.assertIsNotNone(stored.locked_at)
            self.assertEqual(stored.attempts, 2)
            self.assertEqual(stored.last_error, "")
        self.assertEqual(result["failed"], 0)


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
            patch.dict(os.environ, {"EMAIL_DELIVERY_ENABLED": "true"}),
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
    async def test_email_reminder_handler_updates_sent_only_after_delivery(self):
        from src.push import email_push

        now = business_now()
        session = self.Session()
        try:
            course = Course(
                id="reminder-email-course",
                name="邮件提醒课程",
                enroll_start=now + timedelta(minutes=3),
                enroll_end=now + timedelta(hours=2),
                end_time=now + timedelta(days=1),
                capacity=30,
                enrolled=10,
                expired=False,
            )
            subscriber = EmailSubscriber(email="reminder@example.com", verified=True, active=True)
            session.add_all([course, subscriber])
            session.flush()
            reminder = CourseReminder(
                subscriber_id=subscriber.id,
                course_id=course.id,
                remind_before_minutes=5,
                sent=False,
            )
            session.add(reminder)
            session.flush()
            job = enqueue_notification_job(
                session,
                channel="email",
                subscriber_id=subscriber.id,
                subscriber_email=subscriber.email,
                course_ids=[course.id],
                job_type="course_reminder",
                event_type="enroll_reminder",
                delivery_mode="reminder",
                payload={
                    "reminder_id": reminder.id,
                    "expires_at": course.enroll_start.isoformat(),
                },
                dedupe_material=f"reminder_id:{reminder.id}",
            )
            session.commit()
            reminder_id = reminder.id
            job_id = job.id
        finally:
            session.close()

        with (
            patch("src.models.get_session", side_effect=lambda: self.Session()),
            patch.object(email_push, "send_enroll_reminder_email", return_value=False),
        ):
            failed = await email_push.deliver_course_reminder_email_job(job)
        self.assertFalse(failed.success)
        verify = self.Session()
        try:
            self.assertFalse(verify.get(CourseReminder, reminder_id).sent)
        finally:
            verify.close()

        session = self.Session()
        try:
            retry_job = session.get(NotificationJob, job_id)
            retry_job.status = NotificationJobStatus.PROCESSING
            retry_job.attempts = 2
            session.commit()
            retry_job = session.get(NotificationJob, job_id)
        finally:
            session.close()

        with (
            patch("src.models.get_session", side_effect=lambda: self.Session()),
            patch.object(email_push, "send_enroll_reminder_email", return_value=True),
        ):
            succeeded = await email_push.deliver_course_reminder_email_job(retry_job)
        self.assertTrue(succeeded.success)
        verify = self.Session()
        try:
            self.assertTrue(verify.get(CourseReminder, reminder_id).sent)
            events = (
                verify.query(NotificationEvent)
                .filter(NotificationEvent.event_type == "enroll_reminder")
                .order_by(NotificationEvent.id)
                .all()
            )
            self.assertEqual([event.success for event in events], [False, True])
            self.assertEqual({event.channel for event in events}, {"email"})
            self.assertEqual({event.course_id for event in events}, {"reminder-email-course"})
        finally:
            verify.close()

    async def test_daily_summary_handler_sends_once_and_logs_each_course(self):
        from src.models import PushLog
        from src.push import telegram_bot

        now = business_now()
        session = self.Session()
        try:
            live = Course(
                id="summary-live",
                name="汇总课程",
                enroll_start=now + timedelta(days=1),
                enroll_end=now + timedelta(days=2),
                end_time=now + timedelta(days=3),
                capacity=30,
                enrolled=1,
                expired=False,
            )
            gone = Course(
                id="summary-gone",
                name="已结束课程",
                enroll_start=now - timedelta(days=3),
                enroll_end=now - timedelta(days=2),
                end_time=now - timedelta(days=1),
                capacity=30,
                enrolled=1,
                expired=False,
            )
            session.add_all([live, gone])
            session.flush()
            job = enqueue_notification_job(
                session,
                channel="telegram",
                course_ids=["summary-live", "summary-gone"],
                job_type="daily_summary",
                delivery_mode="digest_daily",
                payload={"summary_date": now.strftime("%Y-%m-%d")},
                dedupe_material="summary_date:test",
            )
            session.commit()
        finally:
            session.close()

        with (
            patch("src.models.get_session", side_effect=lambda: self.Session()),
            patch.object(telegram_bot, "send_daily_summary_notification", new=AsyncMock(return_value=True)) as send_mock,
        ):
            result = await telegram_bot.deliver_daily_summary_telegram_job(job)

        self.assertTrue(result.success)
        self.assertEqual(result.delivered_count, 1)
        send_mock.assert_awaited_once()
        self.assertEqual([course.id for course in send_mock.await_args.args[0]], ["summary-live"])
        verify = self.Session()
        try:
            logs = verify.query(PushLog).filter(PushLog.push_type == "daily_telegram").all()
            self.assertEqual([log.course_id for log in logs], ["summary-live"])
        finally:
            verify.close()

    async def test_telegram_reminder_handler_skips_expired_job_without_sending(self):
        from src.push import telegram_bot

        now = business_now()
        session = self.Session()
        try:
            course = Course(
                id="reminder-telegram-course",
                name="Telegram提醒课程",
                enroll_start=now - timedelta(minutes=1),
                enroll_end=now + timedelta(hours=2),
                end_time=now + timedelta(days=1),
                capacity=30,
                enrolled=10,
                expired=False,
            )
            subscriber = EmailSubscriber(email="telegram@example.com", verified=True, active=True)
            session.add_all([course, subscriber])
            session.flush()
            reminder = CourseReminder(
                subscriber_id=subscriber.id,
                course_id=course.id,
                remind_before_minutes=5,
                sent=False,
            )
            session.add(reminder)
            session.flush()
            job = enqueue_notification_job(
                session,
                channel="telegram",
                subscriber_id=subscriber.id,
                course_ids=[course.id],
                job_type="course_reminder",
                event_type="enroll_reminder",
                delivery_mode="reminder",
                payload={
                    "reminder_id": reminder.id,
                    "expires_at": course.enroll_start.isoformat(),
                },
                dedupe_material=f"reminder_id:{reminder.id}",
            )
            session.commit()
            reminder_id = reminder.id
        finally:
            session.close()

        with (
            patch("src.models.get_session", side_effect=lambda: self.Session()),
            patch.object(telegram_bot, "send_reminder_telegram", new=AsyncMock()) as send_mock,
        ):
            result = await telegram_bot.deliver_course_reminder_telegram_job(job)

        self.assertTrue(result.success)
        self.assertEqual(result.delivered_count, 0)
        send_mock.assert_not_awaited()
        verify = self.Session()
        try:
            self.assertTrue(verify.get(CourseReminder, reminder_id).sent)
        finally:
            verify.close()


if __name__ == "__main__":
    unittest.main()
