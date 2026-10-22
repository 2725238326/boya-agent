"""SMTP behavior verified with fake transports only."""

import os
import smtplib
import unittest
from unittest.mock import Mock, patch

from src.email_policy import email_delivery_enabled
from src.push import email_push as mail


class EmailTransportTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "APP_ENV": "production", "NOTIFICATION_MODE": "smtp",
            "EMAIL_DELIVERY_ENABLED": "true", "EMAIL_DELIVERY_SCOPE": "all",
            "SMTP_SERVER": "smtp.example.test", "SMTP_PORT": "587", "SMTP_USE_TLS": "true",
            "SMTP_USERNAME": "sender@example.test", "SMTP_PASSWORD": "test-only",
            "SMTP_PROXY": "", "SMTP_VERIFY_SERVER": "", "SMTP_VERIFY_PORT": "",
            "SMTP_VERIFY_USERNAME": "", "SMTP_VERIFY_PASSWORD": "",
            "SMTP_NOTIFY_SERVER": "", "SMTP_NOTIFY_PORT": "", "SMTP_NOTIFY_USERNAME": "",
            "SMTP_NOTIFY_PASSWORD": "", "SMTP_FROM": "", "SMTP_FROM_VERIFY": "",
            "SMTP_FROM_LOGIN": "", "SMTP_FROM_NOTIFY": "", "SMTP_FROM_REMINDER": "",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_auth_scope_blocks_notify_before_smtp_lookup(self):
        with patch.dict(os.environ, {"EMAIL_DELIVERY_SCOPE": "auth"}):
            self.assertTrue(email_delivery_enabled("login"))
            self.assertTrue(email_delivery_enabled("verify"))
            for kind in ("notify", "reminder", "summary", "broadcast", "unknown"):
                with patch.object(mail, "_get_smtp_config", side_effect=AssertionError("SMTP accessed")):
                    self.assertFalse(mail._send_raw_email("recipient@example.test", "subject", "body", kind))
        with patch.dict(os.environ, {"EMAIL_DELIVERY_SCOPE": "invalid"}):
            self.assertFalse(email_delivery_enabled("login"))

    def test_cleanup_failure_after_data_is_not_retried(self):
        server = Mock()
        server.send_message.return_value = {}
        server.close.side_effect = OSError("cleanup failed")
        with patch.object(mail.smtplib, "SMTP", return_value=server):
            self.assertTrue(mail._send_raw_email("recipient@example.test", "Subject\ntext", "<p>你好</p><a href='https://example.test'>打开</a>"))
        server.send_message.assert_called_once()
        server.close.assert_called_once()
        self.assertIsNotNone(server.starttls.call_args.kwargs["context"])
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "Subject text")
        self.assertTrue(message["Date"])
        self.assertTrue(message["Message-ID"])
        self.assertEqual([part.get_content_type() for part in message.get_payload()], ["text/plain", "text/html"])
        self.assertIn("https://example.test", message.get_payload()[0].get_payload(decode=True).decode())

    def test_auth_failure_closes_connection_and_does_not_retry_same_transport(self):
        server = Mock()
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"private data")
        with patch.object(mail.smtplib, "SMTP", return_value=server) as factory:
            self.assertFalse(mail._send_raw_email("recipient@example.test", "subject", "body"))
        self.assertEqual(factory.call_count, 1)
        server.close.assert_called_once()

    def test_auth_scope_sends_over_verified_implicit_tls(self):
        server = Mock()
        server.send_message.return_value = {}
        with patch.dict(os.environ, {"EMAIL_DELIVERY_SCOPE": "auth", "SMTP_VERIFY_USE_TLS": "false"}), patch.object(
            mail.smtplib, "SMTP_SSL", return_value=server
        ) as factory:
            self.assertTrue(mail._send_raw_email("recipient@example.test", "login", "body", "login"))
        self.assertIsNotNone(factory.call_args.kwargs["context"])
        self.assertEqual(factory.call_args.kwargs["timeout"], 10)
        server.close.assert_called_once()

    def test_proxy_failure_closes_socket_and_does_not_connect_directly(self):
        sock = Mock()
        sock.recv.return_value = b"HTTP/1.1 407 Proxy Auth Required\r\n\r\n"
        with patch.dict(os.environ, {"SMTP_PROXY": "http://proxy.example.test:8080"}), patch.object(
            mail.socket, "create_connection", return_value=sock
        ), patch.object(mail.smtplib, "SMTP", side_effect=AssertionError("direct SMTP")):
            self.assertFalse(mail._send_raw_email("recipient@example.test", "subject", "body"))
        sock.close.assert_called_once()

    def test_proxy_header_does_not_swallow_smtp_greeting(self):
        pending = bytearray(b"HTTP/1.1 200 Connection Established\r\n\r\n220 SMTP greeting\r\n")
        sock = Mock()

        def receive(size):
            chunk = bytes(pending[:size])
            del pending[:size]
            return chunk

        sock.recv.side_effect = receive
        with patch.dict(os.environ, {"SMTP_PROXY": "http://proxy.example.test:8080"}), patch.object(
            mail.socket, "create_connection", return_value=sock
        ):
            self.assertIs(mail._create_proxy_socket("smtp.example.test", 587), sock)
        self.assertEqual(bytes(pending), b"220 SMTP greeting\r\n")

    def test_same_account_with_different_group_is_not_a_fallback(self):
        config = mail._get_smtp_config()
        self.assertIsNone(mail._resolve_fallback_transport(config, "notify", mail._resolve_transport(config, "notify")))

    def test_distinct_fallback_uses_same_message_id_and_closes_both_connections(self):
        primary, backup = Mock(), Mock()
        primary.send_message.side_effect = OSError("fixture failure")
        backup.send_message.return_value = {}
        with patch.dict(os.environ, {"SMTP_VERIFY_SERVER": "backup.example.test"}), patch.object(
            mail.smtplib, "SMTP", side_effect=[primary, backup]
        ) as factory:
            self.assertTrue(mail._send_raw_email("recipient@example.test", "subject", "body"))
        self.assertEqual(factory.call_count, 2)
        primary.close.assert_called_once()
        backup.close.assert_called_once()
        self.assertEqual(primary.send_message.call_args.args[0]["Message-ID"],
                         backup.send_message.call_args.args[0]["Message-ID"])

    def test_readiness_never_exposes_smtp_credentials(self):
        with patch.object(mail.socket, "create_connection", side_effect=AssertionError("network")):
            report = mail.email_readiness()
        self.assertTrue(report["transport_configured"]["login"])
        self.assertNotIn("sender@example.test", str(report))
        self.assertNotIn("test-only", str(report))

    def test_invalid_addresses_and_config_fail_without_network(self):
        with patch.object(mail.smtplib, "SMTP", side_effect=AssertionError("network")):
            for address in ("a@example.test,b@example.test", "a@example.test\nBcc:x", "invalid"):
                self.assertFalse(mail._send_raw_email(address, "subject", "body"))
            with patch.dict(os.environ, {"SMTP_PORT": "invalid"}):
                self.assertFalse(mail._send_raw_email("recipient@example.test", "subject", "body"))
