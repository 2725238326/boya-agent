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


async def observe(page, *, preflight_only=False) -> str:
    print("auth_phase: open_course_entry", flush=True)
    await page.goto(BYKC_COURSE_URL, wait_until="networkidle", timeout=30_000)
    if not _is_sso_login_page(page.url):
        return "course_reached_without_submission" if await is_logged_in(page) else "unexpected_initial_page"

    # Playwright's frame name can come from an iframe ID as well as its name.
    target = page.frame(name="loginIframe") or page
    print("auth_phase: find_password_form", flush=True)
    account = target.locator("#unPassword").first
    if not await account.is_visible():
        tab = target.get_by_text("密码登录", exact=True).first
        if await tab.is_visible():
            await tab.click(timeout=3_000)
    if not await account.count():
        account = target.locator('input[placeholder*="学工号"], input[name="username"]').first
    await account.wait_for(state="visible", timeout=8_000)
    challenge = target.locator("#cptPassword")
    if await challenge.is_visible() and await challenge.is_enabled():
        return "captcha_required_before_submission"
    if preflight_only:
        return "no_visible_challenge_before_submission"

    username, password = os.getenv("BUAA_USERNAME"), os.getenv("BUAA_PASSWORD")
    if not username or not password:
        return "credentials_missing"
    await account.fill(username, timeout=8_000)
    print("auth_phase: fill_password", flush=True)
    password_input = target.locator("#pwPassword").first
    if not await password_input.count():
        password_input = target.locator('input[placeholder*="密码"], input[type="password"]').first
    await password_input.fill(password, timeout=8_000)
    submit = target.locator('input[value="登录"]')
    if not await submit.count():
        submit = target.locator('button:has-text("登录"), input[type="submit"], input.submit-btn')
    print("auth_phase: submit_once", flush=True)
    await submit.first.click(timeout=5_000)
    print("auth_phase: read_result", flush=True)
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
    top_text = await page.locator("body").inner_text(timeout=5_000)
    login_frame = page.frame(name="loginIframe")
    if login_frame:
        frame_text = await login_frame.locator("body").inner_text(timeout=5_000)
        return classify_prompt(frame_text) or classify_prompt(top_text) or "still_on_sso_no_recognized_prompt"
    return classify_prompt(top_text) or "still_on_sso_no_recognized_prompt"


async def main(*, preflight_only: bool) -> str:
    pw, browser, _, page = await create_browser_context()
    try:
        return await observe(page, preflight_only=preflight_only)
    finally:
        await browser.close()
        await pw.stop()


if __name__ == "__main__":
    logger.remove()
    try:
        preflight_only = os.getenv("AUTH_PREFLIGHT_ONLY") == "true"
        if not preflight_only:
            load_dotenv(".env")
        result = asyncio.run(asyncio.wait_for(main(preflight_only=preflight_only), timeout=70))
    except Exception as error:
        print("upstream_auth_diagnostic:", type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
    print("upstream_auth_diagnostic:", result)
