import asyncio
import base64
from datetime import timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, Course, FilterConfig, NotificationJob, PushLog
from src.push import feishu_bot as feishu
from src.time_utils import now as business_now
from scripts.feishu_config import read_credentials
from scripts.configure_feishu import configure

ENV = {"APP_ENV": "production", "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/fixture-only",
       "FEISHU_WEBHOOK_SECRET": "fixture-signing-secret", "FEISHU_ALERTS_ENABLED": "true", "FEISHU_COURSES_ENABLED": "true"}


class FeishuTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.clock = patch.object(feishu.time, "monotonic", return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.sleep = patch.object(feishu.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        feishu._last_request_at = 0

    def test_signature_matches_independent_vector(self):
        secret, timestamp = "fixture-signing-secret", "1600000000"
        expected = base64.b64encode(hmac.digest(f"{timestamp}\n{secret}".encode(), b"", "sha256")).decode()
        self.assertEqual(feishu.signature(secret, timestamp), expected)

    def response(self, body=b'{"code":0}', status=200):
        response = Mock(status=status)
        response.read.return_value = body
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_signed_json_size_and_success_and_rate_limit(self):
        opener = Mock()
        opener.open.return_value = self.response()
        with patch.object(feishu, "build_opener", return_value=opener):
            self.assertTrue(feishu._send_sync("博雅测试", "alerts"))
            self.assertTrue(feishu._send_sync("博雅测试2", "alerts"))
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["msg_type"], "text")
        self.assertEqual(payload["sign"], feishu.signature(ENV["FEISHU_WEBHOOK_SECRET"], payload["timestamp"]))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
        feishu.time.sleep.assert_called_once_with(1.1)

    def test_rejections_and_invalid_responses_never_count_as_success(self):
        for body in (b'{"code":19021,"msg":"secret"}', b'{"code":"0"}', b'{"code":false}',
                     b'{}', b'not json', b'x' * 65537):
            with self.subTest(body=body[:20]), patch.object(feishu, "build_opener") as factory:
                factory.return_value.open.return_value = self.response(body)
                self.assertFalse(feishu._send_sync("博雅测试", "alerts"))

    def test_invalid_url_disabled_scope_staging_and_oversize_never_connect(self):
        with patch.object(feishu, "build_opener", side_effect=AssertionError("network")):
            for key, value in (("FEISHU_WEBHOOK_URL", "https://evil.test/hook/fixture"),
                               ("FEISHU_WEBHOOK_URL", ENV["FEISHU_WEBHOOK_URL"] + "?secret=x"),
                               ("FEISHU_WEBHOOK_SECRET", ""), ("FEISHU_ALERTS_ENABLED", "false"),
                               ("FEISHU_ALERTS_ENABLED", "yes"), ("APP_ENV", "staging")):
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                    self.assertFalse(feishu._send_sync("博雅测试", "alerts"))
            self.assertFalse(feishu._send_sync("博雅" * 10000, "courses"))
        self.assertNotIn("fixture-only", json.dumps(feishu.readiness()))
        self.assertIsNone(feishu._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_local_credentials_parser_does_not_require_pasting_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "webhook.txt"
            path.write_text(ENV["FEISHU_WEBHOOK_URL"] + "\n" + ENV["FEISHU_WEBHOOK_SECRET"], encoding="utf-8")
            self.assertEqual(read_credentials(path)["FEISHU_WEBHOOK_SECRET"], ENV["FEISHU_WEBHOOK_SECRET"])

    def test_configuration_is_atomic_preserves_other_channels_and_private_rollback(self):
        from dotenv import dotenv_values
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / ".env"
            original = 'EMAIL_DELIVERY_ENABLED=false\nBUAA_PASSWORD="fixture-only"\n# Keep comment\n'
            env_file.write_text(original, encoding="utf-8")
            backup = configure(env_file, root / "backups", True, False)
            self.assertEqual(backup.read_text(encoding="utf-8"), original)
            after = dotenv_values(env_file)
            self.assertEqual(after["EMAIL_DELIVERY_ENABLED"], "false")
            self.assertEqual(after["BUAA_PASSWORD"], "fixture-only")
            self.assertEqual(after["FEISHU_ALERTS_ENABLED"], "true")
            self.assertEqual(after["FEISHU_COURSES_ENABLED"], "false")
            self.assertIn("# Keep comment", env_file.read_text())
            with patch.object(feishu, "_send_sync", return_value=False):
                with self.assertRaises(RuntimeError):
                    configure(env_file, root / "backups", True, True, probe=True)
            self.assertEqual(dotenv_values(env_file), after)

    async def test_blocking_transport_runs_off_the_event_loop(self):
        import threading
        event_thread = threading.get_ident()
        threads = []
        with patch.object(feishu, "_send_sync", side_effect=lambda *args: threads.append(threading.get_ident()) or True):
            self.assertTrue(await feishu.send_text("fixture", "courses"))
        self.assertNotEqual(threads, [event_thread])

    def test_interactive_card_is_signed_and_untrusted_fields_are_plain_text(self):
        from types import SimpleNamespace
        now = business_now()
        course = SimpleNamespace(name="[点我](https://evil.test) <at id=all>所有人</at>", capacity=20, remaining=3,
            check_in_method="", sign_method="直接选课", campus="学院路", category="文化", location="教室",
            enroll_start=now, enroll_end=now, start_time=now, end_time=now, last_seen=now)
        card = feishu._course_card(course, "snipe", "instant")
        self.assertEqual(card["header"]["template"], "orange")
        self.assertEqual(card["elements"][0]["text"], {"tag": "plain_text", "content": course.name})
        self.assertIn("待确认", json.dumps(card, ensure_ascii=False))
        opener = Mock()
        opener.open.return_value = self.response()
        with patch.object(feishu, "build_opener", return_value=opener):
            self.assertTrue(feishu._send_sync("", "courses", card=card))
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"], card)
        self.assertEqual(payload["sign"], feishu.signature(ENV["FEISHU_WEBHOOK_SECRET"], payload["timestamp"]))
        with patch.object(feishu, "build_opener", side_effect=AssertionError("network")):
            self.assertFalse(feishu._send_sync("", "courses", card={"oversize": "大" * 10000}))


class FeishuOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.sessions = patch("src.models.get_session", side_effect=self.Session)
        self.sessions.start()
        self.addCleanup(self.sessions.stop)
        self.claims = patch("src.notification_jobs.get_session", side_effect=self.Session)
        self.claims.start()
        self.addCleanup(self.claims.stop)
        self.filters = patch("src.filters.get_session", side_effect=self.Session)
        self.filters.start()
        self.addCleanup(self.filters.stop)
        self.addCleanup(self.engine.dispose)
        now = business_now()
        with self.Session() as session:
            session.add(FilterConfig(id=1, self_sign_only=True, min_remaining=1))
            session.add(Course(id="fixture-course", name="样例课程", capacity=20, enrolled=15,
                               check_in_method="自主签到", campus="学院路", category="文化", location="教室",
                               enroll_start=now - timedelta(hours=1), enroll_end=now + timedelta(hours=2),
                               start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=2)))
            session.commit()

    def enqueue_course(self, **kwargs):
        with self.Session() as session:
            jobs = feishu.enqueue_courses(session, [session.get(Course, "fixture-course")], **kwargs)
            session.commit()
            return jobs[0]

    async def test_course_dedupes_across_modes_retries_and_logs_success(self):
        job = self.enqueue_course()
        same = self.enqueue_course(delivery_mode="priority")
        self.assertEqual(job.id, same.id)
        with patch.object(feishu, "send_card", new=AsyncMock(return_value=False)):
            result = await feishu.drain_jobs()
        self.assertEqual(result["failed"], 1)
        with self.Session() as session:
            stored = session.get(NotificationJob, job.id)
            self.assertEqual(stored.attempts, 1)
            self.assertGreater(stored.available_at, business_now())
            stored.available_at = business_now() - timedelta(seconds=1)
            session.commit()
        with patch.object(feishu, "send_card", new=AsyncMock(return_value=True)) as send:
            result = await feishu.drain_jobs()
            self.assertEqual(result["delivered_count"], 1)
            self.assertIn("自主签到", json.dumps(send.call_args.args[0], ensure_ascii=False))
            self.assertEqual((await feishu.drain_jobs())["claimed"], 0)
        with self.Session() as session:
            self.assertEqual(session.query(PushLog).filter_by(push_type="feishu").count(), 1)

    async def test_independent_switches_do_not_claim_disabled_jobs(self):
        course = self.enqueue_course()
        feishu.queue_scrape_state(failures=3, status="timeout")
        with patch.dict(os.environ, {"FEISHU_COURSES_ENABLED": "false"}), patch.object(
            feishu, "send_card", new=AsyncMock(return_value=True)
        ) as send:
            result = await feishu.drain_jobs()
            self.assertEqual(result["claimed"], 1)
            self.assertIn("系统告警", json.dumps(send.call_args.args[0], ensure_ascii=False))
        with self.Session() as session:
            self.assertEqual(session.get(NotificationJob, course.id).attempts, 0)
        with patch.dict(os.environ, {"FEISHU_ALERTS_ENABLED": "false", "FEISHU_COURSES_ENABLED": "false"}):
            self.assertEqual((await feishu.drain_jobs())["claimed"], 0)

    async def test_filter_changes_expiry_and_webhook_rotation_skip_before_send(self):
        for change in ("filter", "expired", "rotation"):
            with self.subTest(change=change):
                job = self.enqueue_course(event_type=change)
                with self.Session() as session:
                    course = session.get(Course, "fixture-course")
                    course.expired = change == "expired"
                    course.check_in_method = "常规签到" if change == "filter" else "自主签到"
                    session.commit()
                override = {"FEISHU_WEBHOOK_URL": ENV["FEISHU_WEBHOOK_URL"] + "-rotated"} if change == "rotation" else {}
                with patch.dict(os.environ, override), patch.object(feishu, "send_card", new=AsyncMock()) as send:
                    result = await feishu.drain_jobs()
                    send.assert_not_awaited()
                    self.assertEqual(result["succeeded"], 1)

    async def test_alert_cooldown_and_restart_recovery_are_durable_and_private(self):
        for _ in range(3):
            feishu.queue_scrape_state(failures=3, status="password=private url token")
        with self.Session() as session:
            jobs = session.query(NotificationJob).all()
            self.assertEqual(len(jobs), 1)
            self.assertNotIn("password", jobs[0].payload_json)
            self.assertNotIn(ENV["FEISHU_WEBHOOK_SECRET"], jobs[0].payload_json)
        with patch.object(feishu, "send_card", new=AsyncMock(return_value=True)):
            self.assertEqual((await feishu.drain_jobs())["succeeded"], 1)
        feishu.queue_scrape_state(recovered=True)
        feishu.queue_scrape_state(recovered=True)
        with patch.object(feishu, "send_card", new=AsyncMock(return_value=True)) as send:
            self.assertEqual((await feishu.drain_jobs())["succeeded"], 1)
            self.assertIn("恢复通知", json.dumps(send.call_args.args[0], ensure_ascii=False))
        with self.Session() as session:
            self.assertEqual(session.query(NotificationJob).count(), 2)

    async def test_recovery_prevents_sending_obsolete_failure(self):
        feishu.queue_scrape_state(failures=3, status="timeout")
        feishu.queue_scrape_state(recovered=True)
        with patch.object(feishu, "send_card", new=AsyncMock(return_value=True)) as send:
            result = await feishu.drain_jobs()
            self.assertEqual(result["claimed"], 2)
            send.assert_awaited_once()
            self.assertIn("恢复通知", json.dumps(send.call_args.args[0], ensure_ascii=False))

    async def test_daily_summary_date_is_separate_and_old_tasks_are_skipped(self):
        day = business_now().strftime("%Y-%m-%d")
        self.enqueue_course(delivery_mode="digest_daily", summary_date=day)
        self.enqueue_course(delivery_mode="digest_daily", summary_date=day)
        self.enqueue_course(delivery_mode="digest_daily", summary_date="2000-01-01")
        with patch.object(feishu, "send_card", new=AsyncMock(return_value=True)) as send:
            result = await feishu.drain_jobs()
            self.assertEqual(result["claimed"], 2)
            send.assert_awaited_once()
            self.assertIn("每日课程汇总", json.dumps(send.call_args.args[0], ensure_ascii=False))

    async def test_scraping_queues_feishu_without_using_network_or_enabling_other_channels(self):
        from src import scheduler
        with self.Session() as session, patch.object(feishu, "send_card", new=AsyncMock()) as send:
            config = session.get(FilterConfig, 1)
            config.email_enabled = False
            config.telegram_enabled = False
            course = session.get(Course, "fixture-course")
            self.assertEqual(await scheduler._do_push([course], config, session), 0)
            self.assertEqual(session.query(NotificationJob).filter_by(channel="feishu").count(), 1)
            send.assert_not_awaited()

    async def test_scheduler_drains_feishu_with_email_and_telegram_off(self):
        from src import scheduler
        self.enqueue_course()
        with patch.object(scheduler, "load_filter_config") as config, patch.object(
            feishu, "send_card", new=AsyncMock(return_value=True)
        ) as send:
            config.return_value.email_enabled = False
            config.return_value.telegram_enabled = False
            await scheduler.drain_pending_notification_jobs()
            send.assert_awaited_once()
