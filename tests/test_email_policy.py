"""Mail suspension tests never use a real SMTP connection."""

import os
import unittest
from unittest.mock import patch

from src.email_policy import email_delivery_enabled
from src.push.email_push import _send_raw_email, _send_with_transport
from src.notification_jobs import drain_notification_jobs


class EmailPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_only_explicit_true_enables_delivery(self):
        for value in ("", "false", "1", "yes", "invalid"):
            with self.subTest(value=value), patch.dict(os.environ, {"EMAIL_DELIVERY_ENABLED": value}):
                self.assertFalse(email_delivery_enabled())
        with patch.dict(os.environ, {"EMAIL_DELIVERY_ENABLED": " TRUE "}):
            self.assertTrue(email_delivery_enabled())

    def test_paused_mail_never_resolves_smtp_or_opens_transport(self):
        with patch.dict(os.environ, {"EMAIL_DELIVERY_ENABLED": "false"}), patch(
            "src.push.email_push._get_smtp_config", side_effect=AssertionError("SMTP config accessed")
        ), patch("src.push.email_push._create_proxy_socket", side_effect=AssertionError("network accessed")):
            for kind in ("notify", "reminder", "verify", "login", "summary", "broadcast"):
                self.assertFalse(_send_raw_email("test@example.com", "test", "test", kind))
            self.assertFalse(_send_with_transport(None, {}))

    async def test_paused_queue_does_not_claim_or_consume_attempts(self):
        with patch.dict(os.environ, {"EMAIL_DELIVERY_ENABLED": "false"}), patch(
            "src.notification_jobs.get_session", side_effect=AssertionError("queue accessed")
        ):
            result = await drain_notification_jobs({"email": lambda job: True})
        self.assertEqual(result, {"claimed": 0, "succeeded": 0, "failed": 0, "delivered_count": 0})
