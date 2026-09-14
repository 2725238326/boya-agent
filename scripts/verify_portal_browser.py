"""Offline portal checks: all browser requests use fixtures, never production."""

from pathlib import Path

from flask import Flask, render_template
from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]


def main():
    app = Flask(__name__, template_folder=str(ROOT / "web/templates"))
    app.jinja_env.globals["static_asset"] = lambda name: "/static/" + name
    with app.test_request_context("/portal"):
        html = render_template("portal.html")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width in (390, 1280):
                for scenario in ("empty", "course_error", "session_error", "full", "onboarding"):
                    failed = scenario == "course_error"
                    session_failed = scenario == "session_error"
                    onboarding = scenario == "onboarding"
                    page = browser.new_page(viewport={"width": width, "height": 844})
                    saved_tab = "manage" if session_failed else "removed-tab"
                    page.add_init_script(f"localStorage.setItem('portal_active_tab', '{saved_tab}')")
                    if width == 390:
                        page.add_init_script("localStorage.setItem('portal_banner_dismissed', '1')")
                    errors = []
                    writes = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def respond(route):
                        from urllib.parse import urlparse
                        path = urlparse(route.request.url).path
                        if route.request.method != "GET":
                            writes.append(path)
                            return route.fulfill(json={"success": True})
                        if path == "/portal":
                            return route.fulfill(content_type="text/html", body=html)
                        if path.startswith("/static/"):
                            asset = (ROOT / "web" / path.lstrip("/")).resolve()
                            if asset.is_relative_to(ROOT / "web/static") and asset.is_file():
                                return route.fulfill(path=str(asset))
                        if path == "/api/subscriber/session":
                            if session_failed:
                                return route.fulfill(status=503, json={"success": False, "error": "登录状态暂不可用"})
                            return route.fulfill(json={"success": True, "data": {
                                "email": "fixture@example.com", "show_onboarding": onboarding,
                            }})
                        if path == "/api/courses":
                            if failed:
                                return route.fulfill(status=503, json={"success": False, "error": "测试数据源暂不可用"})
                            courses = [{
                                "id": "fixture-full", "name": "固定样例课程", "category": "文化",
                                "teacher": "测试教师", "campus": "学院路", "location": "测试教室",
                                "remaining": 0, "capacity": 30, "enrolled": 30, "expired": False,
                            }] if scenario == "full" else []
                            return route.fulfill(json={"success": True, "data": courses, "total": len(courses)})
                        if path == "/api/categories":
                            return route.fulfill(json={"success": True, "data": ["文化", "艺术"]})
                        if path == "/api/portal/highlights":
                            upcoming = [{
                                "name": "近期课程", "campus": "学院路", "category": "文化",
                                "remaining": 8, "seconds_left": 3600,
                            }]
                            return route.fulfill(json={"success": True, "data": {
                                "upcoming_courses": upcoming,
                                "today_new_count": 2,
                                "pending_reminders": 1,
                            }})
                        if path.startswith("/api/subscriber/session/notifications"):
                            return route.fulfill(json={"success": True, "data": [{
                                "course_name": "近期课程", "course_category": "文化",
                                "event_type": "new", "delivery_mode": "priority",
                                "channel": "email", "success": True, "sent_at": "刚刚",
                            }, {
                                "course_name": "提醒课程", "course_category": "艺术",
                                "event_type": "enroll_reminder", "delivery_mode": "reminder",
                                "channel": "telegram", "success": False, "sent_at": "刚刚",
                            }]})
                        return route.fulfill(status=404, json={"success": False})

                    page.route("**/*", respond)
                    page.goto("http://fixture.test/portal")
                    grid = page.locator("#courseGrid")
                    expect(page.locator("#panel-courses")).to_be_visible()
                    if onboarding:
                        dialog = page.get_by_role("dialog")
                        expect(dialog).to_be_visible()
                        expect(dialog.get_by_role("button", name="先看课程", exact=True)).to_be_focused()
                        page.keyboard.press("Tab")
                        expect(dialog.get_by_role("button", name="去设置偏好", exact=True)).to_be_focused()
                        page.keyboard.press("Tab")
                        expect(dialog.get_by_role("button", name="先看课程", exact=True)).to_be_focused()
                        page.keyboard.press("Shift+Tab")
                        expect(dialog.get_by_role("button", name="去设置偏好", exact=True)).to_be_focused()
                        page.keyboard.press("Escape")
                        expect(page.locator("#portalOnboardingOverlay")).to_be_hidden()
                        expect(page.locator("#tab-courses")).to_be_focused()
                        assert writes == ["/api/subscriber/session/onboarding-seen"], writes
                        assert not errors, errors
                        print(f"PASS width={width} scenario=onboarding: focus trap, escape, focus restore, seen once")
                        page.close()
                        continue
                    if session_failed:
                        expect(grid).to_contain_text("登录状态暂不可用")
                        session_failed = False
                        grid.get_by_role("button", name="重新加载", exact=True).click()
                    if scenario == "full":
                        toggle = page.get_by_role("button", name="已满课程")
                        expect(toggle).to_have_attribute("aria-expanded", "false")
                        toggle.focus()
                        page.keyboard.press("Enter")
                        expect(page.locator("#fullCoursesGrid")).to_be_visible()
                        expect(toggle).to_have_attribute("aria-expanded", "true")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate(
                            "Array.from(document.querySelectorAll('body *')).filter(el => el.getBoundingClientRect().right > innerWidth).map(el => [el.className, el.getBoundingClientRect().width])"
                        )
                        page.keyboard.press("Space")
                        expect(page.locator("#fullCoursesGrid")).to_be_hidden()
                        failed = True
                        page.evaluate("loadPortalData()")
                        expect(grid).to_contain_text("课程数据暂时不可用")
                        expect(page.locator("#fullCoursesSection")).to_have_count(0)
                        assert not errors, errors
                        print(f"PASS width={width} scenario=full: keyboard toggle, stale section removal")
                        page.close()
                        continue
                    expect(grid).to_contain_text("课程数据暂时不可用" if failed else "暂无")
                    expect(grid).to_have_attribute("aria-busy", "false")
                    if not session_failed:
                        summary = page.locator("#upcomingCoursesSummary")
                        expect(summary).to_have_attribute("aria-expanded", "false")
                        expect(summary).to_have_attribute("aria-controls", "upcomingCoursesList")
                        summary.focus()
                        page.keyboard.press("Enter")
                        expect(summary).to_have_attribute("aria-expanded", "true")
                        expect(page.locator("#upcomingCoursesList")).to_be_visible()
                        page.keyboard.press("Space")
                        expect(summary).to_have_attribute("aria-expanded", "false")

                        if width == 390:
                            help_button = page.locator("#portalHelpButton")
                            help_button.click()
                            expect(page.locator("#welcomeBanner")).to_be_visible()
                            expect(help_button).to_have_attribute("aria-expanded", "true")
                            expect(help_button).to_have_class(__import__("re").compile(".*active.*"))
                            page.get_by_role("button", name="我知道了", exact=True).click()
                            page.wait_for_timeout(450)
                            expect(page.locator("#welcomeBanner")).to_be_hidden()
                            expect(help_button).to_have_attribute("aria-expanded", "false")
                            help_button.click()
                            expect(page.locator("#welcomeBanner")).to_be_visible()
                            expect(help_button).to_have_attribute("aria-expanded", "true")
                            page.locator("#portalFeedbackToggle").click()
                            expect(page.locator("#portalFeedbackToggle")).to_have_attribute("aria-expanded", "true")
                            expect(page.locator("#portalFeedbackPanel")).to_have_attribute("aria-hidden", "false")
                            page.locator("#portalFeedbackToggle").click()
                            expect(page.locator("#portalFeedbackToggle")).to_have_attribute("aria-expanded", "false")

                        page.locator("#tab-manage").click()
                        expect(page.get_by_label("校区偏好")).to_have_id("settingsCampus")
                        category = page.locator("#settingsCategories .portal-chip").first
                        expect(category).to_have_role("button")
                        before = category.get_attribute("aria-pressed")
                        category.focus()
                        page.keyboard.press("Enter")
                        expect(category).to_have_attribute("aria-pressed", "false" if before == "true" else "true")

                        page.locator("#tab-notifications").click()
                        timeline = page.locator("#notificationTimeline")
                        expect(timeline).to_contain_text("选课提醒")
                        expect(timeline).to_contain_text("Telegram")
                        expect(timeline).to_contain_text("发送失败")
                        page.locator("#notifyTypeFilter").select_option("enroll_reminder")
                        expect(timeline.locator(".portal-notify-item")).to_have_count(1)
                        expect(timeline).not_to_contain_text("近期课程")
                        page.locator("#notifyTypeFilter").select_option(label="全部类型")
                        expect(timeline.locator(".portal-notify-item")).to_have_count(2)
                        page.locator("#tab-courses").click()
                    assert not errors, errors
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"
                    page.keyboard.press("Tab")
                    assert page.evaluate("document.activeElement !== document.body"), "missing keyboard focus"
                    if failed:
                        failed = False
                        grid.get_by_role("button", name="重新加载", exact=True).click()
                        expect(grid).to_contain_text("当前暂无课程")
                    search = page.locator("#portalSearch")
                    search.fill("fixture-no-match")
                    expect(grid).to_contain_text("当前筛选无结果")
                    grid.get_by_role("button", name="清除筛选", exact=True).click()
                    expect(search).to_have_value("")
                    expect(grid).to_contain_text("当前暂无课程")
                    assert not errors, errors
                    print(f"PASS width={width} scenario={scenario}: state, layout, focus, filter, recovery, JavaScript")
                    page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
