import json
import os
from pathlib import Path
import tempfile
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models import Base, NotificationJob
from src.push import ops_bot as ops
from src.time_utils import now as business_now

OPS_ENV = {"APP_ENV": "production", "TELEGRAM_OPS_ENABLED": "true",
           "TELEGRAM_OPS_BOT_TOKEN": "12345:fixture_only_token_1234567890", "TELEGRAM_OPS_CHAT_ID": "-12345"}


class OpsTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, OPS_ENV)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        for target in ("src.models.get_session", "src.notification_jobs.get_session"):
            wrapper = patch(target, side_effect=self.Session)
            wrapper.start()
            self.addCleanup(wrapper.stop)
        self.addCleanup(self.engine.dispose)

    async def test_cooldown_restart_recovery_and_private_payload(self):
        for _ in range(3):
            ops.queue_state(failures=3, status="password=should-not-persist")
        with self.Session() as session:
            jobs = session.query(NotificationJob).all()
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].channel, "telegram_ops")
            self.assertNotIn("password", jobs[0].payload_json)
            self.assertNotIn(OPS_ENV["TELEGRAM_OPS_BOT_TOKEN"], jobs[0].payload_json)
        with patch.object(ops, "_send_sync", return_value=True):
            self.assertEqual((await ops.drain_jobs())["delivered_count"], 1)
        ops.queue_state(recovered=True)
        ops.queue_state(recovered=True)
        with patch.object(ops, "_send_sync", return_value=True) as send:
            self.assertEqual((await ops.drain_jobs())["delivered_count"], 1)
            self.assertIn("恢复通知", send.call_args.args[0])
        with self.Session() as session:
            self.assertEqual(session.query(NotificationJob).count(), 2)

    async def test_recovery_supersedes_unsent_failure_and_components_are_separate(self):
        ops.queue_state(failures=3)
        ops.queue_state(recovered=True)
        ops.queue_state(failures=3, component="service", status="service_unavailable")
        with patch.object(ops, "_send_sync", return_value=True) as send:
            result = await ops.drain_jobs()
            self.assertEqual(result["claimed"], 3)
            self.assertEqual(result["delivered_count"], 2)
            self.assertEqual(send.call_count, 2)

    async def test_failure_backoff_disabled_and_rotated_targets(self):
        ops.queue_state(failures=3)
        with patch.object(ops, "_send_sync", return_value=False):
            self.assertEqual((await ops.drain_jobs())["failed"], 1)
        with self.Session() as session:
            job = session.query(NotificationJob).one()
            self.assertEqual(job.attempts, 1)
            job.available_at = business_now() - timedelta(seconds=1)
            session.commit()
        with patch.dict(os.environ, {"TELEGRAM_OPS_ENABLED": "false"}):
            self.assertEqual((await ops.drain_jobs())["claimed"], 0)
        with patch.dict(os.environ, {"TELEGRAM_OPS_CHAT_ID": "-99999"}), patch.object(ops, "_send_sync") as send:
            self.assertEqual((await ops.drain_jobs())["delivered_count"], 0)
            send.assert_not_called()

    def test_transport_validates_business_success_and_staging_never_connects(self):
        opener = Mock()
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener.open.return_value = response
        with patch.object(ops, "build_opener", return_value=opener):
            for body, expected in ((b'{"ok":true}', True), (b'{"ok":false}', False), (b'{"ok":1}', False), (b'{}', False)):
                response.read.return_value = body
                self.assertEqual(ops._send_sync("运维验收"), expected)
            payload = json.loads(opener.open.call_args.args[0].data)
            self.assertNotIn("parse_mode", payload)
        with patch.dict(os.environ, {"APP_ENV": "staging"}), patch.object(ops, "build_opener") as factory:
            self.assertFalse(ops._send_sync("test", probe=True))
            ops.queue_state(failures=3)
            factory.assert_not_called()
        self.assertNotIn(OPS_ENV["TELEGRAM_OPS_BOT_TOKEN"], json.dumps(ops.readiness()))

    def test_channel_configuration_probes_before_mutation_and_preserves_mail(self):
        from dotenv import dotenv_values
        from scripts.configure_feishu import configure
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'FEISHU_WEBHOOK_URL': 'https://open.feishu.cn/open-apis/bot/v2/hook/fixture-only',
            'FEISHU_WEBHOOK_SECRET': 'fixture-signing-secret',
            'TELEGRAM_OPS_PROXY_URL': 'http://fixture-proxy.test:8080'
        }):
            root = Path(directory)
            env_file = root / '.env'
            original = 'EMAIL_DELIVERY_ENABLED=false\nBUAA_PASSWORD=fixture-only\n'
            env_file.write_text(original)
            with patch('src.push.feishu_bot._send_sync', return_value=True), patch.object(ops, '_send_sync', return_value=False):
                with self.assertRaises(RuntimeError):
                    configure(env_file, root / 'backups', False, True, ops=True, probe=True)
            self.assertEqual(env_file.read_text(), original)
            with patch('src.push.feishu_bot._send_sync', return_value=True), patch.object(ops, '_send_sync', return_value=True):
                configure(env_file, root / 'backups', False, True, ops=True, probe=True)
            values = dotenv_values(env_file)
            self.assertEqual(values['TELEGRAM_OPS_ENABLED'], 'true')
            self.assertEqual(values['FEISHU_ALERTS_ENABLED'], 'false')
            self.assertEqual(values['EMAIL_DELIVERY_ENABLED'], 'false')
            self.assertEqual(values['TELEGRAM_OPS_PROXY_URL'], 'http://fixture-proxy.test:8080')
