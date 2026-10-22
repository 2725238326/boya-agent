import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from src.runtime import configure_staging, notification_mode, validate_staging
from src.staging_fixtures import SCENARIOS, fixture_outcome
from src.push.email_push import _send_raw_email


class StagingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.root = Path(self.directory.name)
        configure_staging(self.root)

    def test_paths_credentials_and_port_are_fail_closed(self):
        self.assertEqual(validate_staging(), self.root.resolve())
        for key, value in (("DATABASE_PATH", str(self.root.parent / "production.db")),
                           ("BUAA_PASSWORD", "inherited"), ("SMTP_SERVER", "smtp.example.test"),
                           ("WEB_HOST", "0.0.0.0"), ("WEB_PORT", "5000"),
                           ("NOTIFICATION_MODE", "smtp"), ("SCRAPE_SOURCE", "live")):
            with self.subTest(key=key), patch.dict(os.environ, {key: value}):
                with self.assertRaises(RuntimeError):
                    validate_staging()

    def test_dry_run_never_resolves_smtp_and_production_rejects_it(self):
        with patch("src.push.email_push._get_smtp_config", side_effect=AssertionError("SMTP accessed")):
            self.assertTrue(_send_raw_email("fixture@example.test", "preview", "<p>测试</p>", "login"))
        self.assertEqual(len(list((self.root / "mail").glob("*.eml"))), 1)
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaises(RuntimeError):
                notification_mode()

    def test_fixture_statuses_are_explicit(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario), patch.dict(os.environ, {"STAGING_SCENARIO": scenario}):
                outcome = fixture_outcome()
                self.assertEqual(outcome.success, scenario in {"healthy", "empty"})
                self.assertEqual(len(outcome.courses), 4 if scenario == "healthy" else 0)

    def test_launcher_removes_inherited_credentials(self):
        with patch.dict(os.environ, {"BUAA_PASSWORD": "must-not-be-used", "SMTP_PASSWORD": "secret"}):
            configure_staging(self.root)
            self.assertNotIn("BUAA_PASSWORD", os.environ)
            self.assertNotIn("SMTP_PASSWORD", os.environ)

    def test_real_main_once_exit_code_tracks_fixture_result(self):
        script = Path(__file__).resolve().parents[1] / "scripts/run_staging.py"
        for scenario, code in (("healthy", 0), ("timeout", 1)):
            with self.subTest(scenario=scenario):
                result = subprocess.run([sys.executable, str(script), "--once", "--scenario", scenario,
                                         "--root", str(self.root / scenario)], capture_output=True, timeout=30)
                self.assertEqual(result.returncode, code, result.stderr.decode("utf-8", errors="replace"))
