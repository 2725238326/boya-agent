"""
Playwright 爬虫模块 - 抓取博雅课程列表并解析
由于博雅系统 API 返回加密 JSON，使用 Playwright 直接读取渲染后的 DOM
"""

import hashlib
import re
from datetime import datetime
from typing import List, Optional
from loguru import logger
from playwright.async_api import async_playwright, Page, BrowserContext

from src.auth import ensure_logged_in, BYKC_COURSE_URL
from src.models import Course, get_session

# 浏览器数据持久化目录
BROWSER_DATA_DIR = "browser_data"


async def create_browser_context() -> tuple:
    """
    创建持久化浏览器上下文（保存登录态）

    Returns:
        (playwright, browser, context, page) 元组
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"]
    )
    context = await browser.new_context(
        storage_state=None,
        viewport={"width": 1920, "height": 1080},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    return pw, browser, context, page


def generate_course_id(name: str, start_time: str, enroll_start: str = "", teacher: str = "") -> str:
    """Generate a stable course ID from course name and time fields."""
    def _norm(v: str) -> str:
        return re.sub(r"\s+", " ", (v or "").strip()).lower()

    time_key = (start_time or "").strip() or (enroll_start or "").strip() or (teacher or "").strip()
    raw = f"{_norm(name)}_{_norm(time_key)}_{_norm(teacher)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def parse_datetime(text: str) -> Optional[datetime]:
    """解析日期时间字符串，支持多种格式"""
    text = text.strip()
    if not text:
        return None
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    logger.warning(f"无法解析日期时间: {text}")
    return None


def parse_capacity(text: str) -> tuple:
    """解析人数字符串，如 '130/130' -> (130, 130)"""
    text = text.strip()
    if "/" in text:
        parts = text.split("/")
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            pass
    return 0, 0


def _extract_value_after_colon(text: str) -> str:
    """提取冒号后的值，兼容中英文冒号"""
    text = text.strip()
    if "：" in text:
        return text.split("：", 1)[-1].strip()
    if ":" in text:
        return text.split(":", 1)[-1].strip()
    return text


def _extract_datetime_tokens(text: str) -> List[str]:
    """从文本中提取日期时间字符串"""
    pattern = r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?"
    return re.findall(pattern, text)


def _minutes_diff(left: Optional[datetime], right: Optional[datetime]) -> Optional[int]:
    if not left or not right:
        return None
    return abs(int((left - right).total_seconds() // 60))


def _find_similar_active_course(session, data: dict, now: datetime) -> Optional[Course]:
    """Fallback dedupe when the same course drifts by ~1 hour in scraped time fields."""
    name = (data.get("name") or "").strip()
    if not name:
        return None

    teacher = (data.get("teacher") or "").strip()
    location = (data.get("location") or "").strip()
    campus = (data.get("campus") or "").strip()

    candidates = (
        session.query(Course)
        .filter(Course.name == name)
        .filter(Course.expired == False)  # noqa: E712
        .order_by(Course.last_seen.desc())
        .limit(20)
        .all()
    )

    new_start = parse_datetime(data.get("start_time", ""))
    new_enroll_start = parse_datetime(data.get("enroll_start", ""))
    new_enroll_end = parse_datetime(data.get("enroll_end", ""))

    for c in candidates:
        if teacher and c.teacher and c.teacher.strip() != teacher:
            continue
        if location and c.location and c.location.strip() != location:
            continue
        if campus and c.campus and c.campus.strip() != campus:
            continue

        if c.last_seen and (now - c.last_seen).total_seconds() > 48 * 3600:
            continue

        start_gap = _minutes_diff(c.start_time, new_start)
        enroll_start_gap = _minutes_diff(c.enroll_start, new_enroll_start)
        enroll_end_gap = _minutes_diff(c.enroll_end, new_enroll_end)

        if start_gap in (0, 60) and enroll_start_gap in (0, 60) and enroll_end_gap in (0, 60):
            return c

    return None


def _cleanup_near_duplicate_courses(session, now: datetime) -> None:
    """Merge near-duplicate active courses (typically 1-hour drift records)."""
    candidates = (
        session.query(Course)
        .filter(Course.expired == False)  # noqa: E712
        .order_by(Course.last_seen.desc())
        .all()
    )

    seen = set()
    for i, base in enumerate(candidates):
        if base.id in seen:
            continue
        for other in candidates[i + 1:]:
            if other.id in seen:
                continue
            if (base.name or "").strip() != (other.name or "").strip():
                continue
            if (base.teacher or "").strip() != (other.teacher or "").strip():
                continue
            if (base.location or "").strip() != (other.location or "").strip():
                continue
            if (base.campus or "").strip() != (other.campus or "").strip():
                continue

            start_gap = _minutes_diff(base.start_time, other.start_time)
            enroll_start_gap = _minutes_diff(base.enroll_start, other.enroll_start)
            enroll_end_gap = _minutes_diff(base.enroll_end, other.enroll_end)

            if not (start_gap in (0, 60) and enroll_start_gap in (0, 60) and enroll_end_gap in (0, 60)):
                continue

            keep, drop = (base, other) if (base.last_seen or now) >= (other.last_seen or now) else (other, base)
            keep.category = keep.category or drop.category
            keep.sign_method = keep.sign_method or drop.sign_method
            keep.check_in_method = keep.check_in_method or drop.check_in_method
            keep.description = keep.description or drop.description
            keep.organizer = keep.organizer or drop.organizer
            keep.capacity = max(keep.capacity or 0, drop.capacity or 0)
            keep.enrolled = max(keep.enrolled or 0, drop.enrolled or 0)
            keep.last_seen = max(keep.last_seen or now, drop.last_seen or now)

            session.delete(drop)
            seen.add(drop.id)
            logger.info(f"Merged near-duplicate course: keep={keep.id}, drop={drop.id}, name={keep.name}")


async def _check_and_recover_session(page: Page) -> bool:
    """
    会话守护：检测 WebVPN/博雅系统的会话超时弹窗或重定向页面
    如果检测到，自动点击确认/跳转按钮恢复会话
    返回 True 表示页面正常可用
    """
    from src.auth import _is_sso_login_page, ensure_logged_in, BYKC_COURSE_URL

    current_url = page.url

    # 检查是否被弹出到 SSO 登录页
    if _is_sso_login_page(current_url):
        logger.warning("会话已过期，需要重新登录...")
        return await ensure_logged_in(page)

    # 检查是否有「点击跳转」「继续访问」等弹窗/遮罩
    try:
        for btn_text in ["跳转", "继续", "确定", "确认", "前往", "点击"]:
            btn = page.locator(f'button:has-text("{btn_text}"), a:has-text("{btn_text}")')
            if await btn.count() > 0:
                visible = await btn.first.is_visible()
                if visible:
                    logger.info(f"检测到跳转/确认按钮: '{btn_text}'，自动点击...")
                    await btn.first.click()
                    await page.wait_for_timeout(2000)
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    break
    except Exception as e:
        logger.debug(f"检查弹窗: {e}")

    # 检查是否有弹出的 dialog / modal
    try:
        modal = page.locator('.modal:visible, .dialog:visible, [class*="modal"]:visible')
        if await modal.count() > 0:
            close_btn = modal.locator(
                'button:has-text("确定"), button:has-text("确认"), '
                'button:has-text("关闭"), .close'
            )
            if await close_btn.count() > 0:
                await close_btn.first.click()
                logger.info("已关闭弹窗")
                await page.wait_for_timeout(1000)
    except Exception:
        pass

    # 检查校园网限制
    try:
        body = await page.inner_text("body")
        if "校园网" in body and "访问" in body:
            logger.warning("被校园网限制拦截，需要通过 WebVPN 重新登录")
            return await ensure_logged_in(page)
    except Exception:
        pass

    return True


async def _ensure_session_with_retry(page: Page, stage: str, retries: int = 2) -> bool:
    """带重试的会话检查与恢复"""
    for attempt in range(1, retries + 2):
        ok = await _check_and_recover_session(page)
        if ok:
            return True
        if attempt <= retries:
            logger.warning(f"[{stage}] 会话恢复失败，第 {attempt}/{retries + 1} 次重试")
            await page.wait_for_timeout(1500)
    logger.error(f"[{stage}] 会话恢复失败，已达到重试上限")
    return False


async def scrape_courses(page: Page) -> List[dict]:
    """
    从博雅选课页面抓取课程信息
    """
    from src.auth import BYKC_HOME_URL
    courses = []

    try:
        # ====== 导航到博雅首页（SPA 不支持直接 URL 跳转子页面）======
        logger.info(f"导航到博雅首页: {BYKC_HOME_URL}")
        await page.goto(BYKC_HOME_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

        # ====== 会话守护 ======
        if not await _ensure_session_with_retry(page, "进入首页后"):
            logger.error("会话恢复失败")
            return []

        # ====== 通过菜单导航到选课页面 ======
        # 步骤 1: 展开「我的课程」父菜单
        logger.info("展开「我的课程」菜单...")
        try:
            my_course_menu = page.locator('li:has-text("我的课程"), span:has-text("我的课程")')
            if await my_course_menu.count() > 0:
                await my_course_menu.first.click()
                await page.wait_for_timeout(1500)
                logger.info("已点击「我的课程」")
        except Exception as e:
            logger.warning(f"展开菜单失败: {e}")

        # 步骤 2: 点击「选择课程」子菜单（通过 href 属性精确定位）
        logger.info("点击「选择课程」...")
        try:
            # 优先用 href 定位（最精确）
            select_menu = page.locator('li[href="/system/course-select"], a[href="/system/course-select"]')
            if await select_menu.count() == 0:
                # 展开后子菜单应该可见了，用 visible 文本定位
                select_menu = page.locator('li:has-text("选择课程"):visible, span:has-text("选择课程"):visible')
            
            if await select_menu.count() > 0:
                await select_menu.first.click()
                await page.wait_for_timeout(5000)
                await page.wait_for_load_state("networkidle", timeout=15000)
                logger.info(f"已点击「选择课程」，当前 URL: {page.url}")
            else:
                logger.warning("未找到「选择课程」菜单项")
        except Exception as e:
            logger.warning(f"点击选择课程失败: {e}")

        # 截图
        await page.screenshot(path="logs/scrape_page.png", full_page=True)
        logger.info("选课页面截图已保存")

        # 等待课程表格加载
        try:
            await page.wait_for_selector("table", timeout=15000)
            logger.info("课程表格已加载")
        except Exception:
            logger.warning("等待表格超时，保存 HTML 用于调试...")
            html = await page.content()
            with open("logs/scrape_page.json", "w", encoding="utf-8") as f:
                f.write(html)
            return []

        # 逐页解析并抓取详情，避免分页后详情链接错位
        while True:
            if not await _ensure_session_with_retry(page, "分页抓取中"):
                logger.error("会话恢复失败，停止抓取")
                break

            page_courses = await _parse_course_table(page)
            logger.info(f"当前页解析到 {len(page_courses)} 条课程")

            if page_courses:
                page_courses = await _enrich_with_details(page, page_courses)
                courses.extend(page_courses)

            has_next = await _go_to_next_page(page)
            if not has_next:
                break

    except Exception as e:
        logger.error(f"抓取课程列表失败: {e}")
        try:
            await page.screenshot(path="logs/scrape_error.png", full_page=True)
        except Exception:
            pass

    logger.info(f"共抓取到 {len(courses)} 条课程")
    return courses


async def _enrich_with_details(page: Page, courses: List[dict]) -> List[dict]:
    """
    点击每门课的「详细介绍」获取详情页信息（签到方式、课程介绍等）
    """
    logger.info(f"开始抓取 {len(courses)} 门课程的详情...")

    for i, course in enumerate(courses):
        try:
            row_index = course.get("__row_index")
            if row_index is None:
                logger.warning(f"课程[{i}] 缺少行索引，跳过详情抓取")
                continue

            row = page.locator("table tbody tr").nth(row_index)
            detail_link = row.locator('a:has-text("详细介绍"), td:has-text("详细介绍") a')
            if await detail_link.count() == 0:
                logger.debug(f"课程[{i}] 未找到详细介绍链接")
                continue

            # 点击详细介绍
            await detail_link.first.click()
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle", timeout=15000)

            # 解析详情页
            try:
                body_text = await page.inner_text("body")

                # 提取签到方式
                for line in body_text.split("\n"):
                    line = line.strip()
                    if "签到方式" in line:
                        sign_val = _extract_value_after_colon(line)
                        if sign_val:
                            course["check_in_method"] = sign_val
                            logger.info(f"  课程[{i}] 签到方式: {sign_val}")

                    elif "课程分类" in line:
                        cat_val = _extract_value_after_colon(line)
                        if cat_val:
                            course["category"] = cat_val

                    elif "课程组织负责人" in line and "电话" not in line:
                        org_val = _extract_value_after_colon(line)
                        if org_val:
                            course["organizer"] = org_val

                # 提取课程介绍（段落文本）
                try:
                    intro_section = page.locator('text=课程介绍')
                    if await intro_section.count() > 0:
                        # 获取课程介绍后面的文本
                        parent = intro_section.first.locator("..")
                        next_text = await parent.inner_text()
                        desc = next_text.replace("课程介绍", "").strip()[:500]
                        if desc:
                            course["description"] = desc
                except Exception:
                    pass

            except Exception as e:
                logger.debug(f"解析详情页失败: {e}")

            # 点击「返回」回到列表
            back_btn = page.locator('a:has-text("返回"), button:has-text("返回")')
            if await back_btn.count() > 0:
                await back_btn.first.click()
            else:
                await page.go_back()
            await page.wait_for_timeout(2000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_selector("table", timeout=15000)

        except Exception as e:
            logger.warning(f"抓取课程[{i}]详情失败: {e}")
            # 尝试回到列表页
            try:
                back_btn = page.locator('a:has-text("返回")')
                if await back_btn.count() > 0:
                    await back_btn.first.click()
                    await page.wait_for_timeout(2000)
                else:
                    await page.go_back()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

    logger.info("详情抓取完成")
    for course in courses:
        course.pop("__row_index", None)
    return courses


async def _parse_course_table(page: Page) -> List[dict]:
    """解析当前页面的课程表格"""
    courses = []

    # 获取所有课程行（跳过表头）
    rows = await page.query_selector_all("table tbody tr")

    for row_index, row in enumerate(rows):
        try:
            cells = await row.query_selector_all("td")
            if len(cells) < 8:
                continue

            # 提取各列文本
            cell_texts = []
            for cell in cells:
                text = await cell.inner_text()
                cell_texts.append(text.strip())

            # 根据截图中的列顺序解析
            # 状态 | 课程名称 | 课程类别 | 课程信息 | 课程时间 | 开放群体 | 选课时间 | 课程作业 | 课程人数 | 操作
            status_text = cell_texts[0] if len(cell_texts) > 0 else ""
            name = cell_texts[1] if len(cell_texts) > 1 else ""
            category = cell_texts[2] if len(cell_texts) > 2 else ""

            # 课程信息列：包含地点、教师、学院等（多行）
            course_info = cell_texts[3] if len(cell_texts) > 3 else ""
            location = ""
            teacher = ""
            college = ""
            for line in course_info.split("\n"):
                line = line.strip()
                if line.startswith("地点"):
                    location = line.replace("地点：", "").replace("地点:", "").strip()
                elif line.startswith("教师"):
                    teacher = line.replace("教师：", "").replace("教师:", "").strip()
                elif line.startswith("学院"):
                    college = line.replace("学院：", "").replace("学院:", "").strip()

            # 课程时间列：开始和结束
            # 格式如 "开始：2026-03-04 19:00\n结束：2026-03-04 21:00"
            time_info = cell_texts[4] if len(cell_texts) > 4 else ""
            start_time_str = ""
            end_time_str = ""
            for line in time_info.split("\n"):
                line = line.strip()
                if "开始" in line:
                    # 只按第一个中文冒号分割，保留后面的时间如 "2026-03-04 19:00"
                    start_time_str = line.split("：", 1)[-1].strip() if "：" in line else line.split(":", 1)[-1].strip() if ":" in line else line
                elif "结束" in line:
                    end_time_str = line.split("：", 1)[-1].strip() if "：" in line else line.split(":", 1)[-1].strip() if ":" in line else line

            # 开放群体列：校区、学院、年级等
            group_info = cell_texts[5] if len(cell_texts) > 5 else ""
            campus = ""
            open_college = ""
            open_grade = ""
            open_group = ""
            for line in group_info.split("\n"):
                line = line.strip()
                if line.startswith("校区"):
                    campus = line.replace("校区：", "").replace("校区:", "").strip()
                elif line.startswith("学院"):
                    open_college = line.replace("学院：", "").replace("学院:", "").strip()
                elif line.startswith("年级"):
                    open_grade = line.replace("年级：", "").replace("年级:", "").strip()
                elif line.startswith("人群"):
                    open_group = line.replace("人群：", "").replace("人群:", "").strip()

            # 选课时间列
            # 格式如 "选课方式：直接选课\n选课开始：2026-03-03 18:00\n选课截止：2026-03-04 18:00\n退选截止：2026-03-04 18:00"
            enroll_info = cell_texts[6] if len(cell_texts) > 6 else ""
            sign_method = ""
            enroll_start_str = ""
            enroll_end_str = ""
            for line in enroll_info.split("\n"):
                line = line.strip()
                normalized = line.replace(" ", "")
                value = _extract_value_after_colon(line)

                if "选课方式" in normalized:
                    sign_method = value
                    continue

                if "退选" in normalized:
                    continue

                if any(key in normalized for key in ["选课开始", "报名开始", "开始时间"]):
                    enroll_start_str = value
                    continue

                if any(key in normalized for key in ["选课截止", "选课结束", "报名截止", "截止时间"]):
                    enroll_end_str = value
                    continue

                if "选课时间" in normalized:
                    dt_tokens = _extract_datetime_tokens(value)
                    if dt_tokens:
                        enroll_start_str = dt_tokens[0]
                        if len(dt_tokens) > 1:
                            enroll_end_str = dt_tokens[1]
                    continue

            # 课程作业
            has_homework = cell_texts[7] if len(cell_texts) > 7 else ""

            # 课程人数
            capacity_text = cell_texts[8] if len(cell_texts) > 8 else ""
            enrolled, capacity = parse_capacity(capacity_text)

            course_id = generate_course_id(name, start_time_str, enroll_start_str, teacher)

            course_data = {
                "id": course_id,
                "name": name,
                "category": category,
                "location": location,
                "teacher": teacher,
                "college": college,
                "start_time": start_time_str,
                "end_time": end_time_str,
                "enroll_start": enroll_start_str,
                "enroll_end": enroll_end_str,
                "sign_method": sign_method,
                "capacity": capacity,
                "enrolled": enrolled,
                "status": status_text,
                "campus": campus,
                "open_college": open_college,
                "open_grade": open_grade,
                "open_group": open_group,
                "has_homework": has_homework,
                "__row_index": row_index,
            }
            courses.append(course_data)

        except Exception as e:
            logger.warning(f"解析课程行失败: {e}")
            continue

    return courses


async def _go_to_next_page(page: Page) -> bool:
    """尝试翻到下一页，返回是否成功"""
    try:
        # 截图中可见「上一页」「1」「下一页」按钮
        next_btn = page.locator(
            'a:has-text("下一页"), '
            'button:has-text("下一页"), '
            'li:has-text("下一页") a'
        )
        if await next_btn.count() > 0:
            # 检查是否禁用
            first_btn = next_btn.first
            classes = await first_btn.get_attribute("class") or ""
            is_disabled = await first_btn.is_disabled() or "disabled" in classes
            if not is_disabled:
                await first_btn.click()
                await page.wait_for_timeout(3000)
                await page.wait_for_load_state("networkidle", timeout=10000)
                logger.info("已翻到下一页")
                return True
            else:
                logger.info("已到最后一页")
    except Exception as e:
        logger.debug(f"翻页操作: {e}")
    return False


def save_courses_to_db(courses_data: List[dict]) -> List[str]:
    """
    将抓取到的课程保存到数据库，返回新发现课程的 ID 列表。
    同时检测退课捡漏（之前满→现在有名额），保存到全局 _reopened_course_ids。
    """
    global _reopened_course_ids
    session = get_session()
    new_course_ids = []
    _reopened_course_ids = []

    try:
        now = datetime.now()
        for data in courses_data:
            existing = session.query(Course).filter_by(id=data["id"]).first()
            now = datetime.now()
            if not existing:
                existing = _find_similar_active_course(session, data, now)
            enroll_end_dt = parse_datetime(data.get("enroll_end", ""))
            is_expired = bool(enroll_end_dt and enroll_end_dt < now)

            if existing:
                # 检测退课捡漏：之前满了(remaining==0)，现在有名额了
                old_remaining = max(0, existing.capacity - existing.enrolled)
                new_enrolled = data.get("enrolled", existing.enrolled)
                new_capacity = data.get("capacity", existing.capacity)
                new_remaining = max(0, new_capacity - new_enrolled)

                if old_remaining == 0 and new_remaining > 0 and not is_expired:
                    _reopened_course_ids.append(existing.id)
                    logger.info(f"🔥 退课捡漏: [{existing.name}] 新增 {new_remaining} 个名额!")

                # 更新已有课程信息
                existing.name = data.get("name", existing.name)
                existing.category = data.get("category", existing.category)
                existing.location = data.get("location", existing.location)
                existing.teacher = data.get("teacher", existing.teacher)
                existing.college = data.get("college", existing.college)
                existing.start_time = parse_datetime(data.get("start_time", "")) or existing.start_time
                existing.end_time = parse_datetime(data.get("end_time", "")) or existing.end_time
                existing.enroll_start = parse_datetime(data.get("enroll_start", "")) or existing.enroll_start
                existing.enroll_end = enroll_end_dt or existing.enroll_end
                existing.sign_method = data.get("sign_method", existing.sign_method)
                existing.enrolled = new_enrolled
                existing.capacity = new_capacity
                existing.status = data.get("status", existing.status)
                existing.campus = data.get("campus", existing.campus)
                existing.open_college = data.get("open_college", existing.open_college)
                existing.open_grade = data.get("open_grade", existing.open_grade)
                existing.open_group = data.get("open_group", existing.open_group)
                existing.has_homework = data.get("has_homework", existing.has_homework)
                existing.check_in_method = data.get("check_in_method", existing.check_in_method)
                existing.description = data.get("description", existing.description)
                existing.organizer = data.get("organizer", existing.organizer)
                existing.expired = is_expired
                existing.last_seen = now
            else:
                # 新课程
                course = Course(
                    id=data["id"],
                    name=data["name"],
                    category=data.get("category", ""),
                    location=data.get("location", ""),
                    teacher=data.get("teacher", ""),
                    college=data.get("college", ""),
                    start_time=parse_datetime(data.get("start_time", "")),
                    end_time=parse_datetime(data.get("end_time", "")),
                    enroll_start=parse_datetime(data.get("enroll_start", "")),
                    enroll_end=enroll_end_dt,
                    sign_method=data.get("sign_method", ""),
                    capacity=data.get("capacity", 0),
                    enrolled=data.get("enrolled", 0),
                    status=data.get("status", ""),
                    campus=data.get("campus", ""),
                    open_college=data.get("open_college", ""),
                    open_grade=data.get("open_grade", ""),
                    open_group=data.get("open_group", ""),
                    has_homework=data.get("has_homework", ""),
                    check_in_method=data.get("check_in_method", ""),
                    description=data.get("description", ""),
                    organizer=data.get("organizer", ""),
                    first_seen=datetime.now(),
                    last_seen=now,
                    pushed=False,
                    expired=is_expired,
                )
                session.add(course)
                new_course_ids.append(data["id"])

        _cleanup_near_duplicate_courses(session, now)
        session.commit()
        extra = f", {len(_reopened_course_ids)} 门退课捡漏" if _reopened_course_ids else ""
        logger.info(f"数据库更新完成: {len(new_course_ids)} 条新课程, "
                     f"{len(courses_data) - len(new_course_ids)} 条已有课程已更新{extra}")
    except Exception as e:
        session.rollback()
        logger.error(f"保存课程到数据库失败: {e}")
    finally:
        session.close()

    return new_course_ids


# 全局变量：退课捡漏课程 ID（由 save_courses_to_db 设置, run_scrape_task 消费）
_reopened_course_ids = []
