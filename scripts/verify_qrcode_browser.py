"""Offline qrcode page checks: all browser requests use fixtures, never production."""

import base64
import tempfile
from pathlib import Path

from flask import Flask, render_template
from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

COURSE = {
    "id": "fixture-course",
    "name": "固定样例课程",
    "category": "文化",
    "teacher": "测试教师",
    "campus": "学院路",
    "location": "测试教室 101",
    "course_time": "2026-09-23 14:30 ~ 16:30",
    "check_in_label": "自主签到",
    "expired": False,
    "remaining": 5,
}

CONTEXT = {
    "success": True,
    "data": {
        "logged_in": True,
        "email": "fi***@example.com",
        "stats": {"masked_email": "fi***@example.com", "total_uploads": 2, "next_reward_threshold": 3},
        "reward_thresholds": [3, 10, 30],
        "course": COURSE,
        "leaderboard_current": {
            "period": "weekly",
            "period_label": "09/21 - 09/27",
            "items": [{"rank": 1, "masked_email": "fi***@example.com", "upload_count": 2}],
        },
        "leaderboard_all_time": {
            "period": "all",
            "period_label": "累计",
            "items": [{"rank": 1, "masked_email": "fi***@example.com", "upload_count": 2}],
        },
    },
}

UPLOAD_ITEM = {
    "course_name": "固定样例课程",
    "course_time": "2026-09-23 14:30 ~ 16:30",
    "course_location": "测试教室 101",
    "notes": "扫码直接签到",
    "image_url": "/qrcode/uploads/fixture.png",
    "masked_contributor_email": "fi***@example.com",
    "contributor_upload_count": 2,
}


def main():
    app = Flask(__name__, template_folder=str(ROOT / "web/templates"))
    app.jinja_env.globals["static_asset"] = lambda name: "/static/" + name
    with app.test_request_context("/QRcode/course/fixture-course"):
        html = render_template(
            "qrcode.html",
            reward_thresholds=(3, 10, 30),
            qrcode_course=COURSE,
            qrcode_missing_course=False,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        upload_file = Path(tmp_dir) / "fixture-qr.png"
        upload_file.write_bytes(PNG_BYTES)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                for width in (390, 1280):
                    for scenario in ("items", "empty"):
                        page = browser.new_page(viewport={"width": width, "height": 844})
                        errors = []
                        posts = []
                        uploaded = {"done": False}
                        page.on("pageerror", lambda error: errors.append(str(error)))

                        def respond(route):
                            from urllib.parse import urlparse
                            path = urlparse(route.request.url).path
                            if route.request.method == "POST" and path == "/api/qrcode/uploads":
                                posts.append(path)
                                uploaded["done"] = True
                                return route.fulfill(json={
                                    "success": True,
                                    "message": "上传成功，等待审核后才会出现在共享列表",
                                    "data": UPLOAD_ITEM,
                                    "stats": {"masked_email": "fi***@example.com", "total_uploads": 3, "next_reward_threshold": 3},
                                })
                            if route.request.method != "GET":
                                return route.fulfill(json={"success": True})
                            if path.startswith("/QRcode"):
                                return route.fulfill(content_type="text/html", body=html)
                            if path.startswith("/static/"):
                                asset = (ROOT / "web" / path.lstrip("/")).resolve()
                                if asset.is_relative_to(ROOT / "web/static") and asset.is_file():
                                    return route.fulfill(path=str(asset))
                            if path == "/api/qrcode/context":
                                if uploaded["done"]:
                                    import copy
                                    updated = copy.deepcopy(CONTEXT)
                                    updated["data"]["stats"]["total_uploads"] = 3
                                    return route.fulfill(json=updated)
                                return route.fulfill(json=CONTEXT)
                            if path == "/api/qrcode/uploads":
                                items = [UPLOAD_ITEM] if scenario == "items" else []
                                return route.fulfill(json={"success": True, "data": items, "total": len(items)})
                            if path.startswith("/qrcode/uploads/"):
                                return route.fulfill(content_type="image/png", body=PNG_BYTES)
                            return route.fulfill(status=404, json={"success": False})

                        page.route("**/*", respond)
                        page.goto("http://fixture.test/QRcode/course/fixture-course")

                        # 课程信息条：名称 + 签到徽章 + 单行元信息，只出现一次
                        strip = page.locator(".qrcode-course-strip")
                        expect(strip).to_contain_text("固定样例课程")
                        expect(strip.locator(".qrcode-checkin-badge").first).to_contain_text("自主签到")
                        expect(strip).to_contain_text("2026-09-23 14:30 ~ 16:30")
                        assert page.locator(".qrcode-course-summary").count() == 0, "course summary duplicated"

                        # 列表在表单之前（扫码是主任务）
                        list_y = page.locator("#qrcodeListSection").bounding_box()["y"]
                        upload_y = page.locator("#qrcodeUploadSection").bounding_box()["y"]
                        assert list_y < upload_y, "upload form should come after the list"

                        # 状态框可被读屏器感知
                        expect(page.locator("#qrcodeStatusBox")).to_have_attribute("aria-live", "polite")

                        # 贡献面板接线（此前元素缺失，数据白拉）
                        expect(page.locator("#qrcodeCurrentUser")).to_contain_text("fi***@example.com")
                        expect(page.locator("#qrcodeUploadCount")).to_contain_text("2")
                        expect(page.locator("#qrcodeCurrentLeaderboardTitle")).to_contain_text("本期贡献榜 · 09/21 - 09/27")
                        expect(page.locator("#qrcodeCurrentLeaderboard")).to_contain_text("fi***@example.com")
                        expect(page.locator("#qrcodeAllTimeLeaderboard")).to_contain_text("fi***@example.com")

                        qr_list = page.locator("#qrcodeList")
                        expect(qr_list).to_have_attribute("aria-busy", "false")
                        if scenario == "empty":
                            expect(qr_list).to_contain_text("还没有二维码")
                        else:
                            # 卡片渲染 + 预览弹窗焦点管理
                            card_button = qr_list.locator(".qrcode-card-image").first
                            card_button.click()
                            dialog = page.get_by_role("dialog")
                            expect(dialog).to_be_visible()
                            expect(page.locator("#qrcodePreviewTitle")).to_contain_text("固定样例课程")
                            expect(page.get_by_role("button", name="关闭", exact=True)).to_be_focused()
                            page.keyboard.press("Escape")
                            expect(page.locator("#qrcodePreview")).to_be_hidden()
                            expect(card_button).to_be_focused()

                        # 拖拽区选图 → 本地预览出现
                        page.locator("#qrcodeImageInput").set_input_files(str(upload_file))
                        expect(page.locator("#qrcodeFilePreview")).to_be_visible()
                        expect(page.locator("#qrcodeFilePreviewName")).to_contain_text("fixture-qr.png")

                        # 提交上传 → POST 发出 → 状态框播报 → 列表刷新
                        page.get_by_role("button", name="上传二维码", exact=True).click()
                        page.wait_for_function("window.__posted === true || document.querySelector('#qrcodeStatusBox').classList.contains('success')")
                        assert posts == ["/api/qrcode/uploads"], posts
                        expect(page.locator("#qrcodeStatusBox")).to_contain_text("等待审核")
                        expect(page.locator("#qrcodeUploadCount")).to_contain_text("3")
                        expect(page.locator("#qrcodeFilePreview")).to_be_hidden()

                        assert not errors, errors
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"
                        print(f"PASS width={width} scenario={scenario}: strip, order, list, preview focus, dropzone, upload, leaderboard")
                        page.close()
            finally:
                browser.close()


if __name__ == "__main__":
    main()
