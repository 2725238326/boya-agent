"""Execute recovery with tiny fake venvs and fake service/git/network commands."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def bash_path():
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parents[1] / "bin/bash.exe" if git else Path("missing")
        return str(candidate) if candidate.is_file() else None
    return shutil.which("bash")


@unittest.skipUnless(bash_path(), "bash is required to exercise the deployment shell")
class ReleaseGuardTests(unittest.TestCase):
    def test_production_shell_syntax(self):
        workflow = (ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
        source = workflow.split("          script: |\n", 1)[1]
        script = "\n".join(line[12:] if line.startswith(" " * 12) else line for line in source.splitlines())
        result = subprocess.run([bash_path(), "-n"], input=script, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_feishu_configuration_shell_syntax(self):
        workflow = (ROOT / ".github/workflows/configure-feishu.yml").read_text(encoding="utf-8")
        source = workflow.split("          script: |\n", 1)[1]
        script = "\n".join(line[12:] if line.startswith(" " * 12) else line for line in source.splitlines())
        result = subprocess.run([bash_path(), "-n"], input=script, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_failure(self, dependencies=False, startup_fails=False, mail_restore_fails=False):
        with tempfile.TemporaryDirectory(prefix="boya-release-") as directory:
            root = Path(directory)
            app_dir = root / "app"
            recovery = root / "recovery"
            (app_dir / "venv").mkdir(parents=True)
            (app_dir / "venv/version").write_text("new")
            recovery.mkdir()
            if dependencies:
                (recovery / "venv").mkdir()
                (recovery / "venv/version").write_text("old")
            script = f'''
set -euo pipefail
source "{(ROOT / 'deploy/release_guard.sh').as_posix()}"
APP_DIR="{app_dir.as_posix()}"
RECOVERY_DIR="{recovery.as_posix()}"
SERVICE_NAME=fixture-service
PREVIOUS_SHA=previous-commit
LOCAL_HEALTHCHECK_URL=http://fixture.invalid/healthz
SERVICE_WAS_ACTIVE=1
RELEASE_MUTATED=1
DEPENDENCIES_TOUCHED={1 if dependencies else 0}
VENV_SNAPSHOT_READY={1 if dependencies else 0}
sudo() {{ echo "service:$*"; {'return 1' if startup_fails else 'return 0'}; }}
git() {{ echo "git:$*"; return 0; }}
curl() {{ echo fixture-health; return 0; }}
sleep() {{ :; }}
restore_email_state() {{ echo mail-restored; {'return 1' if mail_restore_fails else 'return 0'}; }}
cleanup_wheelhouse() {{ echo cleaned; }}
trap release_guard_exit EXIT
# Simulate an install/startup failure after service stop.
false
'''
            result = subprocess.run([bash_path(), "-c", script], capture_output=True, text=True, timeout=20)
            restored = (app_dir / "venv/version").read_text()
            retained = (recovery / "failed-venv/version").exists()
            return result, restored, retained

    def test_browser_install_failure_restores_previous_service(self):
        result, version, _ = self.run_failure()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("switch --detach previous-commit", result.stdout)
        self.assertIn("systemctl start fixture-service", result.stdout)
        self.assertIn("mail-restored", result.stdout)
        self.assertEqual(version, "new")  # dependencies were unchanged

    def test_dependency_failure_restores_old_venv_and_retains_failed_copy(self):
        result, version, retained = self.run_failure(dependencies=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(version, "old")
        self.assertTrue(retained)

    def test_recovery_failure_is_reported(self):
        result, _, _ = self.run_failure(startup_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RECOVERY FAILED", result.stdout)

    def test_mail_restore_failure_does_not_prevent_service_recovery(self):
        result, _, _ = self.run_failure(mail_restore_fails=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("systemctl start fixture-service", result.stdout)
        self.assertIn("Email state restore failed", result.stdout)
