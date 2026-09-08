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
                for failed in (False, True):
                    page = browser.new_page(viewport={"width": width, "height": 844})
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def respond(route):
                        from urllib.parse import urlparse
                        path = urlparse(route.request.url).path
                        if path == "/portal":
                            return route.fulfill(content_type="text/html", body=html)
                        if path.startswith("/static/"):
                            asset = (ROOT / "web" / path.lstrip("/")).resolve()
                            if asset.is_relative_to(ROOT / "web/static") and asset.is_file():
                                return route.fulfill(path=str(asset))
                        if path == "/api/subscriber/session":
                            return route.fulfill(json={"success": True, "data": {
                                "email": "fixture@example.com", "show_onboarding": False,
                            }})
                        if path == "/api/courses":
                            if failed:
                                return route.fulfill(status=503, json={"success": False, "error": "测试数据源暂不可用"})
                            return route.fulfill(json={"success": True, "data": [], "total": 0})
                        if path == "/api/categories":
                            return route.fulfill(json={"success": True, "data": []})
                        if path == "/api/portal/highlights":
                            return route.fulfill(json={"success": True, "data": {}})
                        return route.fulfill(status=404, json={"success": False})

                    page.route("**/*", respond)
                    page.goto("http://fixture.test/portal")
                    grid = page.locator("#courseGrid")
                    expect(grid).to_contain_text("课程数据暂时不可用" if failed else "暂无")
                    expect(grid).to_have_attribute("aria-busy", "false")
                    assert not errors, errors
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"
                    page.keyboard.press("Tab")
                    assert page.evaluate("document.activeElement !== document.body"), "missing keyboard focus"
                    scenario_failed = failed
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
                    print(f"PASS width={width} failed={scenario_failed}: state, layout, focus, filter, recovery, JavaScript")
                    page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
