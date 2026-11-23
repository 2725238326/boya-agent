"""Submit the configured SSO password once and print only a result category."""

import asyncio
import os
import re
import sys

from dotenv import load_dotenv
from loguru import logger
from playwright.async_api import TimeoutError as BrowserTimeout

from src.auth import BYKC_COURSE_URL, _is_sso_login_page, is_logged_in
from src.scraper import create_browser_context


# Only accept specific messages, not labels for other sign-in tabs.
SSO_OUTCOMES = {
    "credentials_rejected": r"(用户名|账号|学工号|密码).{0,18}(不正确|错误|不匹配)|invalid (credentials|password)",
    "rate_limited_or_locked": r"尝试次数过多|登录过于频繁|稍后再试|账号.{0,8}(冻结|锁定)|too many attempts",
    "additional_verification": r"(请输入|输入正确的|请完成|需要).{0,12}(验证码|二次验证|双重认证|安全验证)|验证码.{0,8}(错误|不正确)|身份验证请求",
    "password_update_required": r"密码.{0,12}(已过期|必须修改)|请.{0,6}(修改|重置)密码",
}


def classify_prompt(text: str) -> str | None:
    return next((outcome for outcome, pattern in SSO_OUTCOMES.items()
                 if re.search(pattern, text, re.IGNORECASE)), None)


async def observe(page) -> str:
    username, password = os.getenv("BUAA_USERNAME"), os.getenv("BUAA_PASSWORD")
    if not username or not password:
        return "credentials_missing"
    await page.goto(BYKC_COURSE_URL, wait_until="load", timeout=30_000)
    if not _is_sso_login_page(page.url):
        return "course_reached_without_submission" if await is_logged_in(page) else "unexpected_initial_page"

    iframe = page.locator('iframe[name="loginIframe"]')
    target = iframe.content_frame if await iframe.count() else page
    tab = target.get_by_text("密码登录", exact=True).first
    if await tab.is_visible():
        await tab.click(timeout=3_000)
    await target.locator("#unPassword").fill(username, timeout=8_000)
    await target.locator("#pwPassword").fill(password, timeout=8_000)
    submit = target.locator('input[value="登录"]')
    if not await submit.count():
        submit = target.locator('button:has-text("登录"), input[type="submit"], input.submit-btn')
    await submit.first.click(timeout=5_000)
    await page.wait_for_timeout(3_000)
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except BrowserTimeout:
        # The result may be visible even if the upstream page keeps polling.
        pass
    if await is_logged_in(page):
        return "course_reached"
    if not _is_sso_login_page(page.url):
        return "left_sso_without_course"

    # Inspect text in memory only. Do not log the DOM, URLs, or form state.
    target = iframe.content_frame if await iframe.count() else page
    text = await target.locator("body").inner_text(timeout=5_000)
    return classify_prompt(text) or "still_on_sso_no_recognized_prompt"


async def main() -> str:
    pw, browser, _, page = await create_browser_context()
    try:
        return await observe(page)
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    logger.remove()
    try:
        load_dotenv(".env")
        result = asyncio.run(asyncio.wait_for(main(), timeout=70))
    except Exception as error:
        print("upstream_auth_diagnostic:", type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
    print("upstream_auth_diagnostic:", result)
