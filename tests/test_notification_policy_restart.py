import unittest
from scripts.notification_policy_restart import needs_restart


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.actual = {'FEISHU_WEBHOOK_URL': 'https://fixture.test/hook', 'FEISHU_WEBHOOK_SECRET': 'fixture-secret',
                       'FEISHU_COURSES_ENABLED': 'true', 'FEISHU_ALERTS_ENABLED': 'true'}
        self.desired = {'FEISHU_WEBHOOK_URL': 'https://fixture.test/hook', 'FEISHU_WEBHOOK_SECRET': 'fixture-secret',
                        'COURSES_ENABLED': 'true', 'ALERTS_ENABLED': 'false', 'OPS_ENABLED': 'false'}

    def test_ignored_alert_flag_and_default_disabled_operations_do_not_restart(self):
        self.assertFalse(needs_restart(self.actual, self.desired))

    def test_live_changes_require_restart(self):
        for key, value in (('FEISHU_WEBHOOK_SECRET', 'rotated-secret'), ('FEISHU_WEBHOOK_URL', 'https://other.test/hook'),
                           ('COURSES_ENABLED', 'false'), ('OPS_ENABLED', 'true')):
            with self.subTest(key=key):
                self.assertTrue(needs_restart(self.actual, dict(self.desired, **{key: value})))
        self.assertTrue(needs_restart(dict(self.actual, TELEGRAM_OPS_ENABLED='true'), self.desired))

    def test_unavailable_process_information_fails_closed(self):
        self.assertTrue(needs_restart({}, self.desired))
