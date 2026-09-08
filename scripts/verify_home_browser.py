"""Public browsing and paused-login regression; all requests are local fixtures."""

from pathlib import Path
import argparse
from urllib.parse import urlparse
from flask import Flask, render_template
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshots', action='store_true')
    options = parser.parse_args()
    app = Flask(__name__, template_folder=str(ROOT / 'web/templates'))
    app.jinja_env.globals['static_asset'] = lambda name: '/static/' + name
    with app.test_request_context('/'):
        pages = {path: render_template(name, email_delivery_available=False) for path, name in (
            ('/', 'home.html'), ('/subscribe', 'subscribe.html'))}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for width in (390, 1280):
                page = browser.new_page(viewport={'width': width, 'height': 844})
                errors, writes, course_requests = [], [], []
                failed = False
                empty = False
                page.on('pageerror', lambda error: errors.append(str(error)))

                def respond(route):
                    path = urlparse(route.request.url).path
                    if route.request.method != 'GET':
                        writes.append(path)
                        return route.fulfill(status=503, json={'success': False})
                    if path in pages:
                        return route.fulfill(content_type='text/html', body=pages[path])
                    if path.startswith('/static/'):
                        asset = (ROOT / 'web' / path.lstrip('/')).resolve()
                        if asset.is_relative_to(ROOT / 'web/static') and asset.is_file():
                            return route.fulfill(path=str(asset))
                    if path == '/api/courses':
                        course_requests.append(path)
                        if failed:
                            return route.fulfill(status=503, json={'success': False})
                        courses = [] if empty else [dict(id=str(index), name=name, remaining=remaining,
                            enrollment_open=opened, campus='学院路', category='文化', enroll_start='2026-09-09 12:00',
                            enroll_end='2026-09-10 12:00') for index, (name, remaining, opened) in enumerate([
                                ('可报名样例', 10, True), ('未开选样例', 5, False), ('已满样例', 0, True),
                                ('<img src=x onerror=alert(1)>', 2, True)])]
                        return route.fulfill(json={'success': True, 'data': courses, 'source': {
                            'last_success': '2026-09-08 10:00:00', 'degraded': True}})
                    if path == '/api/public/insights':
                        return route.fulfill(json={'success': True, 'data': {'available_count': 2, 'active_count': 4}})
                    return route.fulfill(status=401, json={'success': False})

                page.route('**/*', respond)
                page.goto('http://fixture.test/')
                grid = page.locator('#publicCourseGrid')
                expect(grid.locator('article')).to_have_count(4)
                expect(grid.locator('img')).to_have_count(0)
                expect(page.locator('#courseSource')).to_contain_text('最近采集异常')
                page.locator('#courseState').select_option('upcoming')
                expect(grid).to_contain_text('未开选样例')
                expect(grid.locator('article')).to_have_count(1)
                page.locator('#courseSearch').fill('不存在')
                expect(page.locator('#courseSummary')).to_contain_text('没有匹配')
                page.get_by_role('button', name='清除筛选').click()
                expect(grid.locator('article')).to_have_count(4)
                assert len(course_requests) == 1, 'local filters should not repeat API requests'
                grid.locator('summary').first.focus()
                page.keyboard.press('Enter')
                expect(grid.locator('details').first).to_have_attribute('open', '')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile overflow'
                if options.screenshots:
                    (ROOT / 'logs').mkdir(exist_ok=True)
                    page.screenshot(path=str(ROOT / 'logs' / f'public-home-{width}.png'), full_page=True)
                failed = True
                page.get_by_role('button', name='重新加载列表').click()
                expect(page.locator('#courseSummary')).to_contain_text('课程加载失败')
                page.locator('#courseSearch').fill('filter-after-error')
                expect(page.locator('#courseSummary')).to_contain_text('课程加载失败')
                failed, empty = False, True
                page.get_by_role('button', name='重新加载列表').click()
                expect(page.locator('#courseSummary')).to_contain_text('暂无课程')
                page.goto('http://fixture.test/subscribe')
                expect(page.locator('#submitBtn')).to_be_disabled()
                expect(page.locator('#loginBtn')).to_be_disabled()
                page.locator('#emailInput').fill('fixture@example.com')
                page.evaluate("document.getElementById('subscribeForm').requestSubmit()")
                page.evaluate("sendLoginLink()")
                assert not writes, writes
                assert not errors, errors
                print(f'PASS public browse width={width}: states, filters, source age, escaping, keyboard, recovery, mail pause')
                page.close()
        finally:
            browser.close()


if __name__ == '__main__':
    main()
