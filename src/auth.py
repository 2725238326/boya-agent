"""
SSO 统一认证自动登录模块（WebVPN 版）
流程: 登录 WebVPN (d.buaa.edu.cn) → SSO 认证 → 进入博雅选课系统
"""

import os
from loguru import logger

# ========== URL 配置 ==========
WEBVPN_BASE = "https://d.buaa.edu.cn"

# 博雅选课系统通过 WebVPN 代理后的地址
BYKC_WEBVPN_BASE = (
    "https://d.buaa.edu.cn/https/"
    "77726476706e69737468656265737421f2ee4a9f69327d517f468ca88d1b203b"
)
BYKC_COURSE_URL = f"{BYKC_WEBVPN_BASE}/system/course-select"
BYKC_HOME_URL = f"{BYKC_WEBVPN_BASE}/system/home"


def _is_sso_login_page(url: str) -> bool:
    """判断 URL 是否是 SSO 登录页（支持 WebVPN 重写后的 URL）"""
    indicators = [
        "sso.buaa.edu.cn",   # 直连 SSO
        "/login?service=",   # WebVPN 代理的 SSO（URL 中含 login?service=）
        "cas_login=true",    # CAS 登录标志
    ]
    return any(indicator in url for indicator in indicators)


async def _detect_and_fill_login_form(page, username: str, password: str) -> bool:
    """
    检测页面上是否有登录表单，如果有则填写并提交
    返回是否执行了登录操作
    """
    try:
        # ====== 关键：登录表单在 iframe 'loginIframe' 里 ======
        login_frame = None
        for frame in page.frames:
            if frame.name == "loginIframe":
                login_frame = frame
                logger.info(f"找到登录 iframe: {frame.url[:80]}...")
                break

        # 如果没找到 iframe，在主 frame 中查找
        target = login_frame if login_frame else page

        # 尝试点击「密码登录」Tab
        try:
            pwd_tab = target.locator('text=密码登录')
            if await pwd_tab.count() > 0:
                await pwd_tab.first.click()
                await page.wait_for_timeout(1000)
                logger.info("已点击「密码登录」Tab")
        except Exception:
            pass

        # 用精确 ID 定位输入框（来自 debug 结果）
        username_input = target.locator('input#unPassword')
        if await username_input.count() == 0:
            # fallback：尝试其他选择器
            for sel in ['input[placeholder*="学工号"]', 'input[name="username"]']:
                loc = target.locator(sel)
                if await loc.count() > 0:
                    username_input = loc
                    break

        if await username_input.count() == 0:
            logger.warning("未找到用户名输入框")
            return False

        logger.info("检测到登录表单，正在填写...")

        # 填写学号
        await username_input.first.click()
        await username_input.first.fill(username)
        logger.info(f"已填写用户名: {username}")

        # 填写密码
        password_input = target.locator('input#pwPassword')
        if await password_input.count() == 0:
            password_input = target.locator('input[placeholder*="密码"]')
        if await password_input.count() == 0:
            password_input = target.locator('input[type="password"]')

        if await password_input.count() > 0:
            await password_input.first.click()
            await password_input.first.fill(password)
            logger.info("已填写密码")
        else:
            logger.warning("未找到密码输入框")
            return False

        # 截图确认已填写
        await page.screenshot(path="logs/form_filled.png")

        # 点击登录按钮
        submit_btn = target.locator('input[value="登录"]')
        if await submit_btn.count() == 0:
            submit_btn = target.locator(
                'button:has-text("登录"), input[type="submit"], '
                'input.submit-btn'
            )
        if await submit_btn.count() > 0:
            await submit_btn.first.click()
            logger.info("已点击登录按钮")
        else:
            # 直接调用 JS 登录函数
            try:
                await login_frame.evaluate("loginPassword()")
                logger.info("已调用 loginPassword() 函数")
            except Exception:
                await password_input.first.press("Enter")
                logger.info("已按 Enter 提交")

        # 等待跳转
        await page.wait_for_timeout(3000)
        await page.wait_for_load_state("networkidle", timeout=20000)

        logger.info(f"登录提交后 URL: {page.url}")
        return True

    except Exception as e:
        logger.error(f"填写登录表单失败: {e}")
        try:
            await page.screenshot(path="logs/form_error.png")
        except Exception:
            pass
        return False


async def is_logged_in(page) -> bool:
    """检查当前页面是否已登录到博雅系统"""
    current_url = page.url

    # 还在 SSO 登录页
    if _is_sso_login_page(current_url):
        logger.info(f"当前处于 SSO 登录页: {current_url[:100]}...")
        return False

    # 被拦截提示"请在校园网环境下访问"
    try:
        body_text = await page.inner_text("body")
        if "校园网" in body_text and "访问" in body_text:
            logger.info("被校园网限制拦截")
            return False
    except Exception:
        pass

    # ====== 核心判断：URL 包含博雅系统 WebVPN 路径 + /system/ ======
    BYKC_PATH = "77726476706e69737468656265737421f2ee4a9f69327d517f468ca88d1b203b"
    if BYKC_PATH in current_url and "/system/" in current_url:
        logger.info(f"已成功进入博雅系统 ✅ (URL 匹配)")
        return True

    # 备用：检查页面标题或文本
    try:
        title = await page.title()
        if "博雅" in title or "BOYA" in title.upper():
            logger.info(f"已成功进入博雅系统 ✅ (标题匹配: {title})")
            return True
    except Exception:
        pass

    logger.warning(f"无法确定登录状态，URL: {current_url[:100]}...")
    return False


async def do_webvpn_and_sso_login(page, username: str, password: str) -> bool:
    """
    完整登录流程: WebVPN → SSO → 博雅系统
    """
    try:
        # ====== 第 1 步: 访问 WebVPN ======
        logger.info(f"[1/4] 访问 WebVPN: {WEBVPN_BASE}")
        await page.goto(WEBVPN_BASE, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        logger.info(f"WebVPN 页面 URL: {page.url}")
        await page.screenshot(path="logs/step1_webvpn.png")

        # ====== 第 2 步: 处理 SSO 登录（可能多次） ======
        max_login_attempts = 3
        for attempt in range(max_login_attempts):
            if _is_sso_login_page(page.url):
                logger.info(f"[2/4] 检测到 SSO 登录页 (第 {attempt+1} 次)，执行登录...")
                await page.screenshot(path=f"logs/step2_sso_attempt{attempt+1}.png")

                did_login = await _detect_and_fill_login_form(page, username, password)
                if not did_login:
                    logger.warning("未找到登录表单")
                    break

                await page.screenshot(path=f"logs/step2_after_login{attempt+1}.png")
            else:
                logger.info(f"[2/4] 当前不在 SSO 登录页: {page.url[:80]}...")
                break

        # ====== 第 3 步: 通过 WebVPN 访问博雅系统 ======
        logger.info(f"[3/4] 访问博雅系统: {BYKC_COURSE_URL}")
        await page.goto(BYKC_COURSE_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        logger.info(f"博雅页面 URL: {page.url}")
        await page.screenshot(path="logs/step3_boya.png")

        # ====== 第 4 步: 处理博雅系统可能的二次 SSO ======
        if _is_sso_login_page(page.url):
            logger.info("[4/4] 博雅系统触发二次 SSO 登录...")
            await _detect_and_fill_login_form(page, username, password)
            await page.wait_for_timeout(3000)
            await page.screenshot(path="logs/step4_boya_after_sso.png")

        # 最终 URL
        final_url = page.url
        logger.info(f"最终 URL: {final_url}")
        await page.screenshot(path="logs/login_final.png")

        return await is_logged_in(page)

    except Exception as e:
        logger.error(f"登录流程失败: {e}")
        try:
            await page.screenshot(path="logs/login_error.png")
        except Exception:
            pass
        return False


async def ensure_logged_in(page, username: str = None, password: str = None) -> bool:
    """确保已登录状态"""
    username = username or os.getenv("BUAA_USERNAME", "")
    password = password or os.getenv("BUAA_PASSWORD", "")

    if not username or not password:
        logger.error("缺少 BUAA_USERNAME 或 BUAA_PASSWORD 环境变量")
        return False

    # 先尝试直接访问
    try:
        logger.info("尝试直接访问博雅系统（WebVPN）...")
        await page.goto(BYKC_COURSE_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
    except Exception as e:
        logger.warning(f"直接访问超时: {e}")

    # 检查是否已在 SSO 页面，直接处理
    if _is_sso_login_page(page.url):
        logger.info("直接访问触发了 SSO 登录，处理中...")
        await _detect_and_fill_login_form(page, username, password)
        await page.wait_for_timeout(3000)

        # 登录后可能需要重新访问博雅
        if not _is_sso_login_page(page.url):
            await page.goto(BYKC_COURSE_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

    if await is_logged_in(page):
        return True

    # 完整登录流程
    logger.info("需要执行完整登录流程...")
    return await do_webvpn_and_sso_login(page, username, password)
