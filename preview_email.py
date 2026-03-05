"""
本地邮件样式预览脚本
直接生成 HTML 文件并用浏览器打开，无需发送邮件
"""
import sys
import os
import webbrowser
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.push.email_push import (
    _build_notification_html,
    _email_shell,
    send_enroll_reminder_email,
)


# ── 模拟课程数据 ──────────────────────────────────────────────
_mock_id = 0

class MockCourse:
    def __init__(self, name, category, teacher, campus, location,
                 check_in_method, remaining, capacity, enrolled,
                 enroll_start, enroll_end=None, start_time=None):
        global _mock_id
        _mock_id += 1
        self.id = _mock_id
        self.name = name
        self.category = category
        self.teacher = teacher
        self.campus = campus
        self.location = location
        self.check_in_method = check_in_method
        self.sign_method = check_in_method
        self.remaining = remaining
        self.capacity = capacity
        self.enrolled = enrolled
        self.enroll_start = enroll_start
        self.enroll_end = enroll_end
        self.start_time = start_time


MOCK_COURSES = [
    MockCourse(
        name="安全急救培训（CPR与AED）",
        category="博雅课程-安全健康",
        teacher="王建国",
        campus="沙河校区",
        location="沙河校区综合楼B205",
        check_in_method="自主签到",
        remaining=12,
        capacity=40,
        enrolled=28,
        enroll_start=datetime(2026, 3, 10, 9, 0),
        enroll_end=datetime(2026, 3, 15, 18, 0),
        start_time=datetime(2026, 3, 20, 14, 0),
    ),
    MockCourse(
        name="《电磁兼容技术前沿》院士课",
        category="博雅课程-德育",
        teacher="李明院士",
        campus="学院路校区",
        location="主楼A区大报告厅",
        check_in_method="常规签到",
        remaining=3,
        capacity=200,
        enrolled=197,
        enroll_start=datetime(2026, 3, 8, 12, 0),
        enroll_end=datetime(2026, 3, 12, 18, 0),
        start_time=datetime(2026, 3, 18, 9, 0),
    ),
    MockCourse(
        name="中国传统文化与现代设计",
        category="博雅课程-美育",
        teacher="陈雅丽",
        campus="学院路校区",
        location="艺术楼301",
        check_in_method="自主签到",
        remaining=0,
        capacity=30,
        enrolled=30,
        enroll_start=datetime(2026, 3, 6, 8, 0),
        enroll_end=datetime(2026, 3, 10, 18, 0),
        start_time=datetime(2026, 3, 15, 16, 0),
    ),
]

BASE_URL = "http://buaayqq.eu.cc"
SUB_TOKEN = "preview-token-abc123"
UNSUB_URL = f"{BASE_URL}/api/unsubscribe/{SUB_TOKEN}"

# ── 生成：课程通知邮件 ────────────────────────────────────────
html_notification = _build_notification_html(
    MOCK_COURSES,
    unsubscribe_url=UNSUB_URL,
    sub_token=SUB_TOKEN,
    base_url=BASE_URL,
)

# ── 生成：选课提醒邮件 ────────────────────────────────────────
remind_course = MOCK_COURSES[0]
enroll_str = remind_course.enroll_start.strftime('%Y-%m-%d %H:%M') if remind_course.enroll_start else '即将'
_EMAIL_ACCENT = "#0071e3"
_EMAIL_MUTED = "#86868b"
_EMAIL_TEXT = "#1d1d1f"

remind_body = f"""
<p style="font-size:16px; font-weight:600; color:{_EMAIL_TEXT}; margin:0 0 8px;">
  {remind_course.name}
</p>
<p style="font-size:14px; color:{_EMAIL_MUTED}; margin:0 0 20px;">
  {remind_course.category} · {remind_course.teacher} · {remind_course.campus}
</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f5f5f7; border-radius:12px; margin-bottom:20px;">
<tr><td style="padding:16px; text-align:center;">
  <p style="margin:0; font-size:14px; color:{_EMAIL_MUTED};">选课开始时间</p>
  <p style="margin:4px 0 0; font-size:24px; font-weight:700; color:{_EMAIL_ACCENT};">{enroll_str}</p>
</td></tr></table>
<p style="font-size:14px; color:{_EMAIL_TEXT}; text-align:center; margin:0;">
  请提前打开博雅选课系统准备选课
</p>"""

html_reminder = _email_shell("选课即将开始", remind_body)

# ── 写出文件 ─────────────────────────────────────────────────
out_notification = os.path.join(os.path.dirname(__file__), "preview_notification.html")
out_reminder = os.path.join(os.path.dirname(__file__), "preview_reminder.html")

with open(out_notification, "w", encoding="utf-8") as f:
    f.write(html_notification)

with open(out_reminder, "w", encoding="utf-8") as f:
    f.write(html_reminder)

print(f"✅ 课程通知邮件  -> {out_notification}")
print(f"✅ 选课提醒邮件  -> {out_reminder}")
print("正在浏览器中打开...")

webbrowser.open(f"file://{out_notification}")
webbrowser.open(f"file://{out_reminder}")
