from datetime import datetime, timedelta
import unittest
from scripts.check_ops_health import source_unhealthy, update_state


class HealthTests(unittest.TestCase):
    def test_three_failures_recovery_and_fresh_samples(self):
        calls = []
        state = {}
        for _ in range(3):
            state = update_state(state, {'service': 'service_unavailable'}, lambda **kwargs: calls.append(kwargs))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['failures'], 3)
        state = update_state(state, {'service': None}, lambda **kwargs: calls.append(kwargs))
        self.assertEqual(state['service'], 0)
        self.assertTrue(calls[-1]['recovered'])

    def test_no_active_courses_does_not_require_high_frequency_scrapes(self):
        now = datetime(2026, 10, 24, 12)
        data = {'source': {'last_success': '2026-10-24 01:00:00', 'degraded': False}, 'data': []}
        self.assertFalse(source_unhealthy(data, now))
        data['data'] = [{'enrollment_open': True}]
        self.assertTrue(source_unhealthy(data, now))
        data['source']['last_success'] = (now - timedelta(minutes=2)).strftime('%Y-%m-%d %H:%M:%S')
        self.assertFalse(source_unhealthy(data, now))
        data['source']['degraded'] = True
        self.assertTrue(source_unhealthy(data, now))
