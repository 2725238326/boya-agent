import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from xml.etree import ElementTree

import src.push.rss_feed as rss_feed
from src.time_utils import now as business_now


class RSSFeedTests(unittest.TestCase):
    def test_empty_rss_and_atom_have_xml_roots(self):
        rss = rss_feed.generate_rss_feed([])
        atom = rss_feed.generate_atom_feed([])
        self.assertEqual("rss", ElementTree.fromstring(rss).tag.rsplit("}", 1)[-1])
        self.assertEqual("feed", ElementTree.fromstring(atom).tag.rsplit("}", 1)[-1])

    @unittest.skipUnless(rss_feed.HAS_FEEDGEN, "feedgen is not installed")
    def test_rss_and_atom_include_normalized_check_in_and_escaped_title(self):
        anchor = business_now()
        course = SimpleNamespace(
            id="rss-course-1",
            category="艺术与人文",
            name="音乐 <入门> & 赏析",
            teacher="测试老师",
            location="主南106",
            campus="学院路校区",
            start_time=anchor + timedelta(days=1),
            end_time=anchor + timedelta(days=1, hours=2),
            enroll_start=anchor,
            enroll_end=anchor + timedelta(days=1),
            check_in_method="自主签到",
            sign_method="直接选课",
            capacity=80,
            enrolled=79,
            remaining=1,
            first_seen=anchor,
        )

        rss = rss_feed.generate_rss_feed([course], "https://buaaboya.top")
        atom = rss_feed.generate_atom_feed([course], "https://buaaboya.top")

        ElementTree.fromstring(rss)
        ElementTree.fromstring(atom)
        self.assertIn("音乐 &lt;入门&gt; &amp; 赏析", rss)
        self.assertIn("自主签课", rss)
        self.assertIn("自主签到", atom)


if __name__ == "__main__":
    unittest.main()
