import os
import unittest
from datetime import timedelta
from urllib.parse import urlparse
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models import Base, EmailSubscriber

os.environ.setdefault("WEB_SECRET_KEY", "test-secret-for-web-security-0123456789")
os.environ.setdefault("ADMIN_USERNAME", "test-admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-admin-password")

import importlib

web_app = importlib.import_module("web.app")
from web.app import app  # noqa: E402


class WebSecurityTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        web_app._login_email_last_sent_at.clear()
        web_app._login_ip_last_sent_at.clear()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_admin_routes_require_application_authentication(self):
        response = self.client.get("/api/config")
        self.assertEqual(401, response.status_code)
        self.assertEqual("admin_auth_required", response.get_json()["code"])

        response = self.client.get("/api/status")
        self.assertEqual(401, response.status_code)
        self.assertEqual("admin_auth_required", response.get_json()["code"])

    def test_public_healthcheck_does_not_require_admin_authentication(self):
        with patch.object(web_app, "get_session", return_value=self.session):
            response = self.client.get("/healthz")

        self.assertEqual(200, response.status_code)
        self.assertEqual({"success": True, "status": "ok"}, response.get_json())
        self.assertEqual("no-store", response.headers["Cache-Control"])

    def test_admin_ui_accepts_configured_basic_auth(self):
        response = self.client.get(
            "/admin",
            headers={
                "Authorization": "Basic dGVzdC1hZG1pbjp0ZXN0LWFkbWluLXBhc3N3b3Jk",
            },
        )
        self.assertEqual(200, response.status_code)

    def test_admin_route_reports_missing_configuration(self):
        with patch.dict(
            os.environ,
            {"ADMIN_USERNAME": "", "ADMIN_PASSWORD": "", "ADMIN_API_TOKEN": ""},
            clear=False,
        ):
            response = self.client.get("/admin")
        self.assertEqual(503, response.status_code)
        self.assertEqual("admin_auth_not_configured", response.get_json()["code"])

    def test_cross_origin_state_change_is_rejected(self):
        response = self.client.post(
            "/api/session/clear",
            headers={"Origin": "https://attacker.example"},
        )
        self.assertEqual(403, response.status_code)
        self.assertEqual("cross_origin_forbidden", response.get_json()["code"])

    def test_legacy_portal_token_is_not_an_authentication_path(self):
        response = self.client.get("/portal?token=old-token")
        self.assertEqual(302, response.status_code)
        self.assertIn("login_required", response.headers["Location"])

    def test_login_request_proves_ownership_and_link_is_one_time(self):
        subscriber = EmailSubscriber(
            email="known@example.com",
            token="server-session-token",
            verified=True,
            active=True,
        )
        self.session.add(subscriber)
        self.session.commit()
        sent = {}

        def capture_login_email(to_email, login_url, login_code, subscribe_url):
            sent.update({
                "to_email": to_email,
                "login_url": login_url,
                "login_code": login_code,
                "subscribe_url": subscribe_url,
            })
            return True

        with patch.object(web_app, "get_session", return_value=self.session), patch(
            "src.push.email_push.send_login_code_email",
            side_effect=capture_login_email,
        ):
            known = self.client.post("/api/login/request", json={"email": "known@example.com"})
            web_app._login_email_last_sent_at.clear()
            web_app._login_ip_last_sent_at.clear()
            unknown = self.client.post("/api/login/request", json={"email": "unknown@example.com"})

            self.assertEqual(200, known.status_code)
            self.assertEqual(200, unknown.status_code)
            self.assertEqual(known.get_json()["message"], unknown.get_json()["message"])
            self.assertNotIn("bridge_ticket", known.get_json())
            self.assertNotIn("portal_token=", known.headers.get("Set-Cookie", ""))
            self.assertEqual("known@example.com", sent["to_email"])

            self.session.expire_all()
            stored = self.session.query(EmailSubscriber).filter_by(email="known@example.com").one()
            self.assertNotEqual(sent["login_code"], stored.login_code)
            raw_token = urlparse(sent["login_url"]).path.rsplit("/", 1)[-1]

            first = self.client.get(f"/api/login/{raw_token}")
            self.assertEqual(302, first.status_code)
            self.assertIn(f"/verify/{raw_token}", first.headers["Location"])
            self.assertNotIn("portal_token=", first.headers.get("Set-Cookie", ""))

            confirm = self.client.post(f"/api/login/{raw_token}/confirm")
            self.assertEqual(200, confirm.status_code)
            self.assertTrue(confirm.get_json()["success"])
            self.assertIn("portal_token=", confirm.headers.get("Set-Cookie", ""))

            second_client = app.test_client()
            second = second_client.post(f"/api/login/{raw_token}/confirm")
            self.assertEqual(404, second.status_code)
            self.assertFalse(second.get_json()["success"])

    def test_verification_code_failures_are_persisted_and_bounded(self):
        subscriber = EmailSubscriber(
            email="pending@example.com",
            token="pending-session-token",
            verified=False,
            active=True,
            verify_code=web_app._hash_one_time_code("123456"),
            verify_code_expires_at=web_app.business_now() + timedelta(minutes=20),
        )
        self.session.add(subscriber)
        self.session.commit()

        with patch.object(web_app, "get_session", return_value=self.session):
            for _ in range(web_app.AUTH_CODE_MAX_ATTEMPTS - 1):
                response = self.client.post(
                    "/api/subscribe/verify-code",
                    json={"email": "pending@example.com", "code": "000000"},
                )
                self.assertEqual(400, response.status_code)

            response = self.client.post(
                "/api/subscribe/verify-code",
                json={"email": "pending@example.com", "code": "000000"},
            )
            self.assertEqual(429, response.status_code)

        self.session.expire_all()
        stored = self.session.query(EmailSubscriber).filter_by(email="pending@example.com").one()
        self.assertIsNone(stored.verify_code)
        self.assertEqual(0, stored.verify_code_attempts)


if __name__ == "__main__":
    unittest.main()
