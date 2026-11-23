import unittest
from pathlib import Path

from scripts.maintain_homepage_icp import ANCHOR, FOOTER, MARKER, change_config, verify


class HomepageIcpMaintenanceTests(unittest.TestCase):
    def test_edge_change_is_reversible_and_homepage_only(self):
        sample = Path(__file__).resolve().parents[1] / "deploy/nginx_boya.conf"
        original = sample.read_text(encoding="utf-8")
        updated = change_config(original)
        self.assertEqual(updated.count(MARKER), 1)
        homepage, remaining = updated.split("location = / {", 1)[1].split("location = /healthz", 1)
        self.assertIn(MARKER, homepage)
        self.assertEqual(remaining, original.split("location = /healthz", 1)[1])
        self.assertEqual(change_config(updated), updated)
        self.assertEqual(change_config(updated, remove=True), original)

    def test_rejects_unmanaged_accept_encoding_and_homepage_drift(self):
        config = 'location = / {\n    proxy_pass $boya_upstream;\n    proxy_set_header Accept-Encoding "";\n}\n'
        with self.assertRaisesRegex(ValueError, "unmanaged"):
            change_config(config)
        with self.assertRaisesRegex(ValueError, "exact homepage"):
            change_config("location / {\n    proxy_pass $boya_upstream;\n}\n")

    def test_validation_requires_a_single_link_in_the_footer(self):
        old_page = f'<main>{ANCHOR}?v=1"></script>'
        verify(old_page, legacy=True)
        with self.assertRaises(ValueError):
            verify(old_page)
        verify(f"<main></main>{FOOTER}")
        with self.assertRaises(ValueError):
            verify(f"{FOOTER}{FOOTER}")
