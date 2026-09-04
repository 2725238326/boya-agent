"""
Playwright 爬虫模块 - 抓取博雅课程列表并解析
由于博雅系统 API 返回加密 JSON，使用 Playwright 直接读取渲染后的 DOM
"""

import asyncio
import hashlib
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from playwright.async_api import (
    async_playwright,
    Page,
    BrowserContext,
    Locator,
    Response,
    TimeoutError as PlaywrightTimeoutError,
)

from src.auth import (
    _is_sso_login_page,
    ensure_logged_in,
    BYKC_COURSE_URL,
    BYKC_HOME_URL,
    safe_url_for_log,
)
from src.models import Course, get_session
from src.scrape_outcome import (
    ScrapeNavigationError,
    ScrapeOutcome,
    ScrapePageNotReadyError,
    ScrapeStatus,
)
from src.time_utils import now as business_now

COURSE_VIEW_CANDIDATES = [
    ("all", ["\u5168\u90e8\u8bfe\u7a0b", "\u8bfe\u7a0b\u5168\u90e8"]),
    ("near", ["\u8fd1\u671f\u8bfe\u7a0b", "\u5373\u5c06\u5f00\u8bfe", "\u5373\u5c06\u5f00\u62a2"]),
    (
        "far",
        [
            "\u8fdc\u671f\u8bfe\u7a0b",
            "\u9884\u544a\u8bfe\u7a0b",
            "\u672a\u5f00\u8bfe",
            "\u672a\u5f00\u59cb\u8bfe\u7a0b",
            "\u5f85\u5f00\u8bfe",
            "\u5f85\u5f00\u8bfe\u7a0b",
        ],
    ),
]
MAX_PAGES_PER_VIEW = 25
COURSE_PAGE_READY_TIMEOUT_MS = 15000
COURSE_PAGE_POLL_INTERVAL_MS = 300
# The upstream course system renders a real page even when the current
# selection window contains no courses. Treat these explicit empty states as
# a successful scrape so the scheduler does not report a false navigation
# failure or retry the same page unnecessarily.
COURSE_PAGE_EMPTY_SELECTORS = (
    ".el-table__empty-block",
    ".el-table__empty-text",
    ".ant-empty",
    ".ant-table-placeholder",
    ".dataTables_empty",
    ".empty-data",
    ".no-data",
    ".no-result",
)
COURSE_PAGE_EMPTY_MARKERS = (
    "暂无课程",
    "暂无可选课程",
    "暂无选课",
    "暂无选课信息",
    "暂无数据",
    "无可选课程",
    "没有可选课程",
    "没有符合条件的课程",
    "暂无符合条件的课程",
    "当前没有课程",
    "当前暂无课程",
    "暂未发布课程",
)
COURSE_PAGE_CONTEXT_MARKERS = ("课程", "选课", "报名")
MIN_FAST_PATH_ROWS = max(3, int(os.getenv("SCRAPER_MIN_FAST_PATH_ROWS", "5")))
SCRAPE_HEALTH_MIN_BASELINE = max(5, int(os.getenv("SCRAPE_HEALTH_MIN_BASELINE", "10")))
SCRAPE_HEALTH_MIN_ROWS = max(3, int(os.getenv("SCRAPE_HEALTH_MIN_ROWS", "5")))
SCRAPE_HEALTH_RATIO = min(1.0, max(0.1, float(os.getenv("SCRAPE_HEALTH_RATIO", "0.35"))))

NETWORK_FIELD_ALIASES = {
    "name": ["name", "courseName", "course_name", "title", "课程名称", "课程名", "kcmc"],
    "category": ["category", "courseType", "course_type", "typeName", "课程类别", "类别", "kclb"],
    "location": ["location", "address", "place", "classroom", "room", "地点", "dd"],
    "teacher": ["teacher", "teacherName", "teacher_name", "lecturer", "教师", "js"],
    "college": ["college", "academy", "school", "学院", "xy"],
    "start_time": ["startTime", "start_time", "beginTime", "begin_time", "开始时间", "开始", "kssj"],
    "end_time": ["endTime", "end_time", "finishTime", "finish_time", "结束时间", "结束", "jssj"],
    "enroll_start": ["enrollStart", "enroll_start", "selectStart", "signupStart", "选课开始", "报名开始"],
    "enroll_end": ["enrollEnd", "enroll_end", "selectEnd", "signupEnd", "选课结束", "选课截止", "报名截止"],
    "status": ["status", "courseStatus", "状态", "zt"],
    "sign_method": ["signMethod", "sign_method", "selectMode", "选课方式"],
    "campus": ["campus", "campusName", "校区"],
    "open_college": ["openCollege", "open_college", "开放学院"],
    "open_grade": ["openGrade", "open_grade", "开放年级"],
    "open_group": ["openGroup", "open_group", "开放人群", "开放对象", "人群"],
    "has_homework": ["hasHomework", "homework", "作业", "课程作业"],
    "check_in_method": ["checkInMethod", "check_in_method", "签到方式"],
    "description": ["description", "courseDesc", "desc", "课程介绍", "简介"],
    "organizer": ["organizer", "owner", "组织者", "负责单位"],
    "capacity": ["capacity", "total", "limit", "maxNum", "max_num", "人数上限", "容量"],
    "enrolled": ["enrolled", "selected", "selectedNum", "selected_num", "已选人数", "已选", "人数"],
}


async def create_browser_context() -> tuple:
    """
    创建当前进程内复用的浏览器上下文（不保证跨重启保存登录态）

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


def _normalize_field_key(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", (text or "")).lower()


def _normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, dict):
        for key in ("label", "name", "text", "title", "value"):
            if key in value:
                return _normalize_scalar(value.get(key))
        return ""
    if isinstance(value, list):
        parts = [_normalize_scalar(item) for item in value]
        return "\n".join(part for part in parts if part)
    return str(value).strip()


def _safe_int_from_scalar(value: Any) -> int:
    text = _normalize_scalar(value)
    if not text:
        return 0
    match = re.search(r"-?\d+", text)
    if not match:
        return 0
    return int(match.group(0))


def _pick_network_value(item: dict, field_name: str) -> Any:
    normalized = {_normalize_field_key(str(key)): value for key, value in item.items()}
    for alias in NETWORK_FIELD_ALIASES.get(field_name, []):
        alias_key = _normalize_field_key(alias)
        if alias_key in normalized:
            return normalized[alias_key]
    return None


def _walk_json_nodes(node: Any, depth: int = 0):
    if depth > 7:
        return
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_json_nodes(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json_nodes(value, depth + 1)


def _looks_like_course_item(item: dict) -> bool:
    keys = {_normalize_field_key(str(key)) for key in item.keys()}
    score = 0
    for alias_group in ("name", "start_time", "enroll_start", "teacher", "location", "status", "capacity"):
        for alias in NETWORK_FIELD_ALIASES.get(alias_group, []):
            if _normalize_field_key(alias) in keys:
                score += 1
                break
    return score >= 2


def _normalize_course_from_network(item: dict) -> Optional[dict]:
    name = _normalize_scalar(_pick_network_value(item, "name"))
    if not name:
        return None

    category = _normalize_scalar(_pick_network_value(item, "category"))
    location = _normalize_scalar(_pick_network_value(item, "location"))
    teacher = _normalize_scalar(_pick_network_value(item, "teacher"))
    college = _normalize_scalar(_pick_network_value(item, "college"))
    start_time = _normalize_scalar(_pick_network_value(item, "start_time"))
    end_time = _normalize_scalar(_pick_network_value(item, "end_time"))
    enroll_start = _normalize_scalar(_pick_network_value(item, "enroll_start"))
    enroll_end = _normalize_scalar(_pick_network_value(item, "enroll_end"))
    status = _normalize_scalar(_pick_network_value(item, "status"))
    sign_method = _normalize_scalar(_pick_network_value(item, "sign_method"))
    campus = _normalize_scalar(_pick_network_value(item, "campus"))
    open_college = _normalize_scalar(_pick_network_value(item, "open_college"))
    open_grade = _normalize_scalar(_pick_network_value(item, "open_grade"))
    open_group = _normalize_scalar(_pick_network_value(item, "open_group"))
    has_homework = _normalize_scalar(_pick_network_value(item, "has_homework"))
    check_in_method = _normalize_scalar(_pick_network_value(item, "check_in_method"))
    description = _normalize_scalar(_pick_network_value(item, "description"))
    organizer = _normalize_scalar(_pick_network_value(item, "organizer"))

    capacity_raw = _pick_network_value(item, "capacity")
    enrolled_raw = _pick_network_value(item, "enrolled")
    capacity_text = _normalize_scalar(capacity_raw)
    enrolled_text = _normalize_scalar(enrolled_raw)

    if capacity_text and "/" in capacity_text:
        enrolled, capacity = parse_capacity(capacity_text)
    elif enrolled_text and "/" in enrolled_text and not capacity_text:
        enrolled, capacity = parse_capacity(enrolled_text)
    else:
        enrolled = _safe_int_from_scalar(enrolled_raw)
        capacity = _safe_int_from_scalar(capacity_raw)

    if not (start_time or enroll_start or campus or capacity or status):
        return None

    course_id = generate_course_id(name, start_time, enroll_start, teacher, location, campus)
    legacy_course_id = generate_legacy_course_id(name, start_time, enroll_start, teacher)
    return {
        "id": course_id,
        "legacy_id": legacy_course_id,
        "name": name,
        "category": category,
        "location": location,
        "teacher": teacher,
        "college": college,
        "start_time": start_time,
        "end_time": end_time,
        "enroll_start": enroll_start,
        "enroll_end": enroll_end,
        "sign_method": sign_method,
        "capacity": capacity,
        "enrolled": enrolled,
        "status": status,
        "campus": campus,
        "open_college": open_college,
        "open_grade": open_grade,
        "open_group": open_group,
        "has_homework": has_homework,
        "check_in_method": check_in_method,
        "description": description,
        "organizer": organizer,
        "__row_index": None,
        "__table_index": 0,
        "__source": "network",
    }


def _extract_courses_from_network_payload(payload: Any) -> List[dict]:
    courses: List[dict] = []
    seen_ids = set()
    for node in _walk_json_nodes(payload):
        if not isinstance(node, list) or not node:
            continue
        if not all(isinstance(item, dict) for item in node):
            continue
        sample_matches = sum(1 for item in node[:8] if _looks_like_course_item(item))
        if sample_matches < max(1, min(2, len(node))):
            continue

        for item in node:
            course = _normalize_course_from_network(item)
            if not course:
                continue
            cid = course.get("id")
            if cid and cid not in seen_ids:
                seen_ids.add(cid)
                courses.append(course)
    return courses


class _JsonResponseRecorder:
    def __init__(self, page: Page):
        self.page = page
        self.payloads: List[Tuple[str, Any]] = []
        self._tasks: List[asyncio.Task] = []

    def start(self) -> None:
        self.page.on("response", self._handle_response)

    async def stop(self) -> None:
        try:
            self.page.remove_listener("response", self._handle_response)
        except Exception:
            pass
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def _handle_response(self, response: Response) -> None:
        url = response.url or ""
        if response.status >= 400:
            return
        resource_type = ""
        try:
            resource_type = response.request.resource_type
        except Exception:
            pass
        if resource_type not in {"fetch", "xhr"} and not any(token in url.lower() for token in ("api", "query", "list", "course")):
            return
        self._tasks.append(asyncio.create_task(self._consume_response(response)))

    async def _consume_response(self, response: Response) -> None:
        try:
            headers = await response.all_headers()
            content_type = (headers.get("content-type") or "").lower()
            if "json" not in content_type and "javascript" not in content_type and "text/plain" not in content_type:
                return
            payload = await response.json()
        except Exception:
            return
        self.payloads.append((response.url, payload))

    def extract_courses(self) -> List[dict]:
        courses: List[dict] = []
        for url, payload in self.payloads:
            try:
                extracted = _extract_courses_from_network_payload(payload)
            except Exception as e:
                logger.debug(f"解析网络课程数据失败: {safe_url_for_log(url)} -> {e}")
                continue
            if extracted:
                logger.info(f"网络响应命中 {len(extracted)} 条课程: {safe_url_for_log(url)}")
                courses.extend(extracted)
        return _dedupe_scraped_courses(courses)


def generate_legacy_course_id(name: str, start_time: str, enroll_start: str = "", teacher: str = "") -> str:
    """Legacy course ID kept for backward-compatible matching with existing rows."""
    def _norm(v: str) -> str:
        return re.sub(r"\s+", " ", (v or "").strip()).lower()

    time_key = (start_time or "").strip() or (enroll_start or "").strip() or (teacher or "").strip()
    raw = f"{_norm(name)}_{_norm(time_key)}_{_norm(teacher)}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def generate_course_id(
    name: str,
    start_time: str,
    enroll_start: str = "",
    teacher: str = "",
    location: str = "",
    campus: str = "",
) -> str:
    """Generate a stable course ID that distinguishes parallel offerings."""
    def _norm(v: str) -> str:
        return re.sub(r"\s+", " ", (v or "").strip()).lower()

    time_key = (start_time or "").strip() or (enroll_start or "").strip() or (teacher or "").strip()
    raw = (
        f"{_norm(name)}_{_norm(time_key)}_{_norm(teacher)}_"
        f"{_norm(location)}_{_norm(campus)}"
    )
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
    """Parse capacity text from variants like 98/200 or labeled count text."""
    text = (text or "").strip()
    if not text:
        return 0, 0

    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        return int(m.group(1)), int(m.group(2))

    nums = re.findall(r"\d+", text)
    if len(nums) >= 2:
        return int(nums[0]), int(nums[1])
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


def _normalize_table_label(text: str) -> str:
    return re.sub(r"[\s:：]+", "", (text or "").strip())


def _match_column_key(header_text: str) -> Optional[str]:
    normalized = _normalize_table_label(header_text)
    alias_map = {
        "status": ["状态"],
        "name": ["课程名称", "课程名"],
        "category": ["课程类别", "类别"],
        "info": ["课程信息", "信息"],
        "time": ["课程时间", "上课时间", "时间"],
        "group": ["开放群体", "开放对象"],
        "enroll": ["选课时间", "报名时间", "选课信息"],
        "homework": ["课程作业", "作业"],
        "capacity": ["课程人数", "人数"],
        "action": ["操作"],
    }
    for key, aliases in alias_map.items():
        if any(alias in normalized for alias in aliases):
            return key
    return None


async def _extract_header_rows_from_table(target) -> List[List[str]]:
    header_rows: List[List[str]] = []
    rows = target.locator("thead tr, tr")
    row_count = min(await rows.count(), 6)

    for row_index in range(row_count):
        row = rows.nth(row_index)
        try:
            cells = row.locator("th, td")
            cell_count = await cells.count()
            if not cell_count:
                continue

            texts: List[str] = []
            for cell_index in range(cell_count):
                cell = cells.nth(cell_index)
                text = re.sub(r"\s+", " ", await cell.inner_text()).strip()
                colspan_attr = await cell.get_attribute("colspan")
                try:
                    colspan = max(1, int(colspan_attr or "1"))
                except Exception:
                    colspan = 1
                for _ in range(colspan):
                    texts.append(text)

            if texts:
                header_rows.append(texts)
        except Exception:
            continue

    return header_rows


async def _build_table_column_map(target) -> dict:
    header_rows = await _extract_header_rows_from_table(target)
    headers = _select_best_header_row(header_rows)
    return _build_column_map_from_headers(headers)


def _get_cell_text(cell_texts: List[str], column_map: dict, key: str, fallback_idx: int) -> str:
    idx = column_map.get(key, fallback_idx)
    return cell_texts[idx] if idx < len(cell_texts) else ""


def _build_column_map_from_headers(headers: List[str]) -> dict:
    column_map = {}
    for idx, header_text in enumerate(headers):
        key = _match_column_key(header_text)
        if key and key not in column_map:
            column_map[key] = idx
    return column_map


def _score_header_row(headers: List[str]) -> int:
    keys = []
    for header_text in headers:
        key = _match_column_key(header_text)
        if key and key not in keys:
            keys.append(key)

    if not keys:
        return 0

    score = len(keys)
    if "name" in keys:
        score += 3
    if "time" in keys:
        score += 2
    if "enroll" in keys:
        score += 2
    if "capacity" in keys:
        score += 1
    if "status" in keys:
        score += 1
    return score


def _select_best_header_row(header_rows: List[List[str]]) -> List[str]:
    best_headers: List[str] = []
    best_score = 0

    for headers in header_rows:
        score = _score_header_row(headers)
        if score > best_score:
            best_headers = headers
            best_score = score

    return best_headers


def _build_course_row_payload(
    cell_texts: List[str],
    column_map: dict,
    row_index: int,
    table_index: int,
) -> Optional[dict]:
    if len(cell_texts) < 6:
        return None

    status_text = _get_cell_text(cell_texts, column_map, "status", 0)
    name = _get_cell_text(cell_texts, column_map, "name", 1)
    category = _get_cell_text(cell_texts, column_map, "category", 2)
    name = (name or "").strip()
    if not name:
        return None

    # 课程信息列：包含地点、教师、学院等（多行）
    course_info = _get_cell_text(cell_texts, column_map, "info", 3)
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
    time_info = _get_cell_text(cell_texts, column_map, "time", 4)
    start_time_str = ""
    end_time_str = ""
    for line in time_info.split("\n"):
        line = line.strip()
        if "开始" in line:
            start_time_str = _extract_value_after_colon(line)
        elif "结束" in line:
            end_time_str = _extract_value_after_colon(line)
    if not start_time_str or not end_time_str:
        dt_tokens = _extract_datetime_tokens(time_info)
        if dt_tokens:
            start_time_str = start_time_str or dt_tokens[0]
            if len(dt_tokens) > 1:
                end_time_str = end_time_str or dt_tokens[1]

    # 开放群体列：校区、学院、年级等
    group_info = _get_cell_text(cell_texts, column_map, "group", 5)
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
    enroll_info = _get_cell_text(cell_texts, column_map, "enroll", 6)
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
    if not enroll_start_str or not enroll_end_str:
        dt_tokens = _extract_datetime_tokens(enroll_info)
        if dt_tokens:
            enroll_start_str = enroll_start_str or dt_tokens[0]
            if len(dt_tokens) > 1:
                enroll_end_str = enroll_end_str or dt_tokens[1]

    has_homework = _get_cell_text(cell_texts, column_map, "homework", 7)
    capacity_text = _get_cell_text(cell_texts, column_map, "capacity", 8)
    enrolled, capacity = parse_capacity(capacity_text)

    course_id = generate_course_id(
        name,
        start_time_str,
        enroll_start_str,
        teacher,
        location,
        campus,
    )
    legacy_course_id = generate_legacy_course_id(name, start_time_str, enroll_start_str, teacher)

    return {
        "id": course_id,
        "legacy_id": legacy_course_id,
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
        "__table_index": table_index,
    }


def _minutes_diff(left: Optional[datetime], right: Optional[datetime]) -> Optional[int]:
    if not left or not right:
        return None
    return abs(int((left - right).total_seconds() // 60))


NEAR_DUPLICATE_RECENT_HOURS = 6


def _same_calendar_day(left: Optional[datetime], right: Optional[datetime]) -> bool:
    return bool(left and right and left.date() == right.date())


def _is_near_duplicate_triplet(
    left_start: Optional[datetime],
    right_start: Optional[datetime],
    left_enroll_start: Optional[datetime],
    right_enroll_start: Optional[datetime],
    left_enroll_end: Optional[datetime],
    right_enroll_end: Optional[datetime],
) -> bool:
    start_gap = _minutes_diff(left_start, right_start)
    enroll_start_gap = _minutes_diff(left_enroll_start, right_enroll_start)
    enroll_end_gap = _minutes_diff(left_enroll_end, right_enroll_end)

    gaps = (start_gap, enroll_start_gap, enroll_end_gap)
    if all(gap == 0 for gap in gaps):
        return True

    # Keep the legacy 1-hour drift fallback, but only when all three time
    # fields stay on the same calendar day. That preserves the old anti-dup
    # behavior without swallowing genuinely new sessions.
    return (
        all(gap == 60 for gap in gaps)
        and _same_calendar_day(left_start, right_start)
        and _same_calendar_day(left_enroll_start, right_enroll_start)
        and _same_calendar_day(left_enroll_end, right_enroll_end)
    )


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

        if c.last_seen and (now - c.last_seen).total_seconds() > NEAR_DUPLICATE_RECENT_HOURS * 3600:
            continue

        if _is_near_duplicate_triplet(
            c.start_time,
            new_start,
            c.enroll_start,
            new_enroll_start,
            c.enroll_end,
            new_enroll_end,
        ):
            logger.info(
                "Reuse near-duplicate course instead of inserting new row: "
                f"existing={c.id}, name={c.name}, start={c.start_time}, "
                f"enroll_start={c.enroll_start}, incoming_start={new_start}, "
                f"incoming_enroll_start={new_enroll_start}"
            )
            return c

    return None


def _legacy_identity_matches(course: Course, data: dict) -> bool:
    if (course.name or "").strip() != (data.get("name") or "").strip():
        return False

    teacher = (data.get("teacher") or "").strip()
    if teacher and (course.teacher or "").strip() and (course.teacher or "").strip() != teacher:
        return False

    location = (data.get("location") or "").strip()
    if location and (course.location or "").strip() and (course.location or "").strip() != location:
        return False

    campus = (data.get("campus") or "").strip()
    if campus and (course.campus or "").strip() and (course.campus or "").strip() != campus:
        return False

    return _is_near_duplicate_triplet(
        course.start_time,
        parse_datetime(data.get("start_time", "")),
        course.enroll_start,
        parse_datetime(data.get("enroll_start", "")),
        course.enroll_end,
        parse_datetime(data.get("enroll_end", "")),
    )


def _cleanup_near_duplicate_courses(session, now: datetime) -> None:
    """Merge near-duplicate active courses (typically 1-hour drift records)."""
    candidates = (
        session.query(Course)
        .filter(Course.expired == False)  # noqa: E712
        .order_by(Course.last_seen.desc())
        .all()
    )

    # 只有四个身份字段都相同的课程才可能合并。先分组再做两两比较，避免
    # 课程量增长后对整张活动课程表做 O(n²) 扫描。
    candidate_groups = {}
    for candidate in candidates:
        identity = tuple(
            (getattr(candidate, field) or "").strip()
            for field in ("name", "teacher", "location", "campus")
        )
        candidate_groups.setdefault(identity, []).append(candidate)

    seen = set()
    for group in candidate_groups.values():
        for i, base in enumerate(group):
            if base.id in seen:
                continue
            for other in group[i + 1:]:
                if other.id in seen:
                    continue

                exact_duplicate = _is_near_duplicate_triplet(
                    base.start_time,
                    other.start_time,
                    base.enroll_start,
                    other.enroll_start,
                    base.enroll_end,
                    other.enroll_end,
                )
                if not exact_duplicate:
                    continue

                newest_seen = max(base.last_seen or now, other.last_seen or now)
                if (
                    _minutes_diff(base.start_time, other.start_time) == 60
                    and (now - newest_seen).total_seconds() > NEAR_DUPLICATE_RECENT_HOURS * 3600
                ):
                    continue

                keep, drop = (base, other) if (base.last_seen or now) >= (other.last_seen or now) else (other, base)
                keep.category = keep.category or drop.category
                keep.sign_method = keep.sign_method or drop.sign_method
                keep.check_in_method = keep.check_in_method or drop.check_in_method
                keep.description = keep.description or drop.description
                keep.organizer = keep.organizer or drop.organizer
                keep.status = keep.status or drop.status
                keep.start_time = keep.start_time or drop.start_time
                keep.end_time = keep.end_time or drop.end_time
                keep.enroll_start = keep.enroll_start or drop.enroll_start
                keep.enroll_end = keep.enroll_end or drop.enroll_end

                keep_capacity = keep.capacity or 0
                drop_capacity = drop.capacity or 0
                keep_enrolled = keep.enrolled or 0
                drop_enrolled = drop.enrolled or 0
                keep_remaining = max(0, keep_capacity - keep_enrolled)
                drop_remaining = max(0, drop_capacity - drop_enrolled)

                # Prefer the snapshot with more remaining seats. This keeps a newly
                # reopened course from being flattened back into a stale full record
                # when two duplicate rows are merged in the same scrape window.
                if drop_remaining > keep_remaining:
                    keep.capacity = drop_capacity
                    keep.enrolled = drop_enrolled
                else:
                    keep.capacity = max(keep_capacity, drop_capacity)

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


async def _course_page_has_empty_state(page: Page) -> bool:
    """Return whether the authenticated course page explicitly says it is empty."""
    if not _is_course_select_url(getattr(page, "url", "")):
        return False

    for selector in COURSE_PAGE_EMPTY_SELECTORS:
        try:
            candidates = page.locator(selector)
            for index in range(await candidates.count()):
                if await candidates.nth(index).is_visible():
                    return True
        except Exception:
            continue

    try:
        body_text = re.sub(r"\s+", "", await page.inner_text("body"))
    except Exception:
        return False

    has_course_context = any(marker in body_text for marker in COURSE_PAGE_CONTEXT_MARKERS)
    has_empty_marker = any(marker in body_text for marker in COURSE_PAGE_EMPTY_MARKERS)
    return has_course_context and has_empty_marker


async def _wait_course_tables_ready(page: Page) -> bool:
    """Wait for a course table or an explicit no-course state to render."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + COURSE_PAGE_READY_TIMEOUT_MS / 1000

    while loop.time() < deadline:
        try:
            if await page.locator("table:visible").count() > 0:
                await page.wait_for_timeout(1200)
                return True
            if await _course_page_has_empty_state(page):
                logger.info("选课页面已加载，当前暂无可选课程")
                return True
        except Exception as exc:
            logger.debug(f"等待选课页面状态失败: {exc}")

        remaining_ms = max(0, int((deadline - loop.time()) * 1000))
        if remaining_ms <= 0:
            break
        await page.wait_for_timeout(min(COURSE_PAGE_POLL_INTERVAL_MS, remaining_ms))

    return False


async def _get_visible_course_tables(page: Page) -> List[Tuple[int, Locator]]:
    """Return visible tables that look like course tables."""
    tables = page.locator("table:visible")
    count = await tables.count()
    result: List[Tuple[int, Locator]] = []

    for idx in range(count):
        table = tables.nth(idx)
        try:
            column_map = await _build_table_column_map(table)
            if "name" in column_map and ("enroll" in column_map or "time" in column_map):
                result.append((idx, table))
        except Exception:
            continue

    return result


async def _extract_visible_course_rows_via_dom(page: Page) -> List[dict]:
    """Fast-path DOM extraction done inside the browser to reduce round-trips."""
    try:
        extracted = await page.evaluate(
            """
            () => {
              const cleanInline = (value) => (value || "")
                .replace(/\\u00a0/g, " ")
                .replace(/\\s+/g, " ")
                .trim();

              const cleanCell = (value) => (value || "")
                .replace(/\\u00a0/g, " ")
                .replace(/\\r/g, "")
                .split("\\n")
                .map((line) => line.replace(/[ \\t]+/g, " ").trim())
                .filter(Boolean)
                .join("\\n");

              const isVisible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
                  return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };

              const expandHeaderTexts = (row) => {
                const texts = [];
                for (const cell of Array.from(row.children)) {
                  if (!cell.matches("th,td")) continue;
                  const text = cleanInline(cell.innerText || cell.textContent);
                  const colspan = Math.max(1, parseInt(cell.getAttribute("colspan") || "1", 10) || 1);
                  for (let i = 0; i < colspan; i += 1) {
                    texts.push(text);
                  }
                }
                return texts;
              };

              const buildBodyRows = (tbodyRows) => {
                const carry = [];
                const rows = [];

                const fillCarry = (rowTexts, cursorRef) => {
                  while (carry[cursorRef.value]) {
                    rowTexts[cursorRef.value] = carry[cursorRef.value].text;
                    carry[cursorRef.value].remaining -= 1;
                    if (carry[cursorRef.value].remaining <= 0) {
                      delete carry[cursorRef.value];
                    }
                    cursorRef.value += 1;
                  }
                };

                tbodyRows.forEach((row, rowIndex) => {
                  if (!isVisible(row)) return;
                  const rowTexts = [];
                  const cursorRef = { value: 0 };
                  fillCarry(rowTexts, cursorRef);

                  for (const cell of Array.from(row.children)) {
                    if (!cell.matches("td,th")) continue;
                    while (rowTexts[cursorRef.value] !== undefined) {
                      cursorRef.value += 1;
                      fillCarry(rowTexts, cursorRef);
                    }

                    const text = cleanCell(cell.innerText || cell.textContent);
                    const colspan = Math.max(1, parseInt(cell.getAttribute("colspan") || "1", 10) || 1);
                    const rowspan = Math.max(1, parseInt(cell.getAttribute("rowspan") || "1", 10) || 1);

                    for (let i = 0; i < colspan; i += 1) {
                      rowTexts[cursorRef.value + i] = text;
                      if (rowspan > 1) {
                        carry[cursorRef.value + i] = { text, remaining: rowspan - 1 };
                      }
                    }

                    cursorRef.value += colspan;
                    fillCarry(rowTexts, cursorRef);
                  }

                  fillCarry(rowTexts, cursorRef);

                  const normalized = Array.from(
                    { length: rowTexts.length },
                    (_, index) => cleanCell(rowTexts[index])
                  );
                  rows.push({ row_index: rowIndex, cells: normalized });
                });

                return rows;
              };

              const scoreHeaderRow = (headers) => {
                const normalize = (value) => (value || "").replace(/[\\s:：]+/g, "");
                const aliasMap = {
                  status: ["状态"],
                  name: ["课程名称", "课程名"],
                  category: ["课程类别", "类别"],
                  info: ["课程信息", "信息"],
                  time: ["课程时间", "上课时间", "时间"],
                  group: ["开放群体", "开放对象"],
                  enroll: ["选课时间", "报名时间", "选课信息"],
                  homework: ["课程作业", "作业"],
                  capacity: ["课程人数", "人数"],
                  action: ["操作"],
                };

                const keys = [];
                for (const headerText of headers) {
                  const normalized = normalize(headerText);
                  if (!normalized) continue;
                  for (const [key, aliases] of Object.entries(aliasMap)) {
                    if (aliases.some((alias) => normalized.includes(normalize(alias)))) {
                      if (!keys.includes(key)) {
                        keys.push(key);
                      }
                    }
                  }
                }

                let score = keys.length;
                if (keys.includes("name")) score += 3;
                if (keys.includes("time")) score += 2;
                if (keys.includes("enroll")) score += 2;
                if (keys.includes("capacity")) score += 1;
                if (keys.includes("status")) score += 1;
                return score;
              };

              const visibleTables = Array.from(document.querySelectorAll("table")).filter(isVisible);
              const payload = [];

              visibleTables.forEach((table, tableIndex) => {
                const theadRows = Array.from(table.querySelectorAll("thead tr")).filter(isVisible);
                const allVisibleRows = Array.from(table.querySelectorAll("tr")).filter(isVisible).slice(0, 6);
                const headerRows = (theadRows.length ? theadRows : allVisibleRows)
                  .map((row) => expandHeaderTexts(row))
                  .filter((row) => row.length > 0);
                if (!headerRows.length) {
                  return;
                }

                const bestScore = Math.max(...headerRows.map((row) => scoreHeaderRow(row)), 0);
                if (bestScore <= 0) {
                  return;
                }

                let tbodyRows = Array.from(table.querySelectorAll("tbody tr"));
                if (!tbodyRows.length) {
                  const allRows = Array.from(table.querySelectorAll("tr")).filter(isVisible);
                  tbodyRows = allRows.slice(Math.min(headerRows.length, allRows.length));
                }
                if (!tbodyRows.length) {
                  return;
                }

                const rows = buildBodyRows(tbodyRows);
                rows.forEach((row) => {
                  payload.push({
                    table_index: tableIndex,
                    row_index: row.row_index,
                    header_rows: headerRows,
                    cells: row.cells,
                  });
                });
              });

              return payload;
            }
            """
        )
    except Exception as e:
        logger.debug(f"DOM 课程表提取失败，回退到 Locator 逐行解析: {e}")
        return []

    courses: List[dict] = []
    for item in extracted or []:
        header_rows = item.get("header_rows") or []
        headers = _select_best_header_row(header_rows) if header_rows else (item.get("headers") or [])
        cells = item.get("cells") or []
        column_map = _build_column_map_from_headers(headers)
        if "name" not in column_map or ("enroll" not in column_map and "time" not in column_map):
            continue
        payload = _build_course_row_payload(
            cells,
            column_map,
            int(item.get("row_index") or 0),
            int(item.get("table_index") or 0),
        )
        if payload:
            courses.append(payload)

    if courses:
        logger.info(f"浏览器内 DOM 提取命中 {len(courses)} 条课程行")
    return courses


async def _try_click_view_alias(page: Page, alias: str) -> bool:
    normalized_alias = re.sub(r"\s+", "", alias)
    selectors = [
        '[role="tab"]',
        'button',
        'a',
        '.tab',
        '.tabs li',
        '.el-tabs__item',
        '.ant-tabs-tab',
        '.layui-tab-title li',
    ]

    for selector in selectors:
        locator = page.locator(selector)
        count = await locator.count()
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible():
                    continue
                text = re.sub(r"\s+", "", await candidate.inner_text())
                if not text or normalized_alias not in text:
                    continue
                await candidate.click(timeout=4000)
                await page.wait_for_timeout(1500)
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await _wait_course_tables_ready(page)
                logger.info(f"Switched course view with alias [{alias}]")
                return True
            except Exception:
                continue
    return False


async def _activate_course_view(page: Page, aliases: List[str]) -> bool:
    for alias in aliases:
        if await _try_click_view_alias(page, alias):
            return True
    return False


async def _click_visible_text_control(page: Page, labels: List[str]) -> bool:
    selectors = [
        "button",
        "a",
        "[role=\"button\"]",
        "li",
        "span",
    ]
    for label in labels:
        for selector in selectors:
            locator = page.locator(f'{selector}:has-text("{label}")')
            count = await locator.count()
            for idx in range(count):
                candidate = locator.nth(idx)
                try:
                    if not await candidate.is_visible():
                        continue
                    await candidate.click(timeout=4000)
                    await page.wait_for_timeout(1500)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    return True
                except Exception:
                    continue
    return False


async def _reset_course_filters(page: Page) -> bool:
    """Best-effort reset for sticky search/select conditions on the course page."""
    if await _click_visible_text_control(page, ["重置", "清空", "全部重置", "全部清空"]):
        await _wait_course_tables_ready(page)
        logger.info("Reset course filters via explicit reset control")
        return True

    try:
        changed = await page.evaluate(
            """
            () => {
              const visible = (el) => {
                if (!el) return false;
                const style = window.getComputedStyle(el);
                if (!style) return false;
                if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") {
                  return false;
                }
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };

              let changed = false;
              for (const input of Array.from(document.querySelectorAll('input[type="text"], input[type="search"], textarea'))) {
                if (!visible(input)) continue;
                const hint = `${input.placeholder || ""} ${input.name || ""} ${input.id || ""} ${input.className || ""}`;
                if (!/[搜查课名关键检索]/.test(hint)) continue;
                if (input.value) {
                  input.value = "";
                  input.dispatchEvent(new Event("input", { bubbles: true }));
                  input.dispatchEvent(new Event("change", { bubbles: true }));
                  changed = true;
                }
              }

              for (const select of Array.from(document.querySelectorAll("select"))) {
                if (!visible(select)) continue;
                const options = Array.from(select.options || []);
                const target = options.find((option) => /全部|不限|请选择/.test((option.textContent || "").trim()));
                if (!target) continue;
                if (select.value !== target.value) {
                  select.value = target.value;
                  select.dispatchEvent(new Event("change", { bubbles: true }));
                  changed = true;
                }
              }

              return changed;
            }
            """
        )
    except Exception as e:
        logger.debug(f"重置课程筛选条件失败: {e}")
        return False

    if not changed:
        return False

    await page.wait_for_timeout(1200)
    if await _click_visible_text_control(page, ["查询", "搜索", "检索", "刷新列表", "刷新"]):
        await _wait_course_tables_ready(page)
    logger.info("Reset course filters via DOM fallback")
    return True


async def _go_to_first_page(page: Page) -> bool:
    """Best-effort pagination reset before scraping a view."""
    if await _click_visible_text_control(page, ["第一页", "首页"]):
        await _wait_course_tables_ready(page)
        logger.info("Reset pagination to the first page via explicit control")
        return True

    # Fallback: keep clicking previous page until it no longer changes.
    for _ in range(8):
        before_signature = await _capture_visible_course_table_signature(page)
        moved = await _click_visible_text_control(page, ["上一页", "上页"])
        if not moved:
            break
        await _wait_course_tables_ready(page)
        after_signature = await _capture_visible_course_table_signature(page)
        if after_signature == before_signature:
            break

    return True


async def _capture_visible_course_table_signature(page: Page) -> str:
    """Build a compact signature of the currently visible course tables."""
    tables = await _get_visible_course_tables(page)
    if not tables:
        return "no-visible-course-table"

    chunks: List[str] = []
    for _, table in tables[:2]:
        try:
            text = re.sub(r"\s+", " ", await table.inner_text()).strip()
        except Exception:
            continue
        if text:
            chunks.append(text[:240])

    if not chunks:
        return f"visible-course-tables:{len(tables)}"
    return f"visible-course-tables:{len(tables)}|" + "||".join(chunks)


async def _load_current_view_page_courses(page: Page) -> List[dict]:
    """Parse the current page, retrying once when the table briefly disappears."""
    page_courses = await _parse_visible_course_tables(page)
    if page_courses:
        return page_courses

    await page.wait_for_timeout(1200)
    return await _parse_visible_course_tables(page)


async def _collect_current_view_courses(
    page: Page,
    include_details: bool,
    view_name: str,
) -> List[dict]:
    """Scrape every page in the currently active course view."""
    courses: List[dict] = []
    page_no = 1

    while True:
        if page_no > MAX_PAGES_PER_VIEW:
            logger.warning(f"视图[{view_name}] 超过 {MAX_PAGES_PER_VIEW} 页，停止翻页以避免死循环")
            break

        if not await _ensure_session_with_retry(page, f"{view_name} 第{page_no}页"):
            logger.error(f"视图[{view_name}] 会话恢复失败，停止抓取该视图")
            break

        page_courses = await _load_current_view_page_courses(page)
        logger.info(f"视图[{view_name}] 第 {page_no} 页解析到 {len(page_courses)} 门课程")

        if page_courses:
            if include_details:
                page_courses = await _enrich_with_details(page, page_courses)
            else:
                for course in page_courses:
                    course.pop("__row_index", None)
                    course.pop("__table_index", None)
            courses.extend(page_courses)
        else:
            logger.warning(f"视图[{view_name}] 当前页没有可见课程表格，停止该视图抓取")
            break

        has_next = await _go_to_next_page(page)
        if not has_next:
            break
        page_no += 1

    return courses


def _merge_scraped_course(base: dict, incoming: dict) -> dict:
    merged = dict(base)
    for key, value in incoming.items():
        if key.startswith("__"):
            continue
        if value in (None, ""):
            continue
        current = merged.get(key)
        if key in {"capacity", "enrolled"}:
            if current in (None, "") or int(value or 0) >= int(current or 0):
                merged[key] = value
        elif current in (None, ""):
            merged[key] = value

    merged_remaining = max(0, int(merged.get("capacity") or 0) - int(merged.get("enrolled") or 0))
    incoming_remaining = max(0, int(incoming.get("capacity") or 0) - int(incoming.get("enrolled") or 0))
    if incoming_remaining > merged_remaining:
        merged["capacity"] = incoming.get("capacity", merged.get("capacity"))
        merged["enrolled"] = incoming.get("enrolled", merged.get("enrolled"))
    return merged


def _dedupe_scraped_courses(courses: List[dict]) -> List[dict]:
    deduped: Dict[str, dict] = {}
    order: List[str] = []

    for course in courses:
        cid = course.get("id")
        if not cid:
            continue
        if cid not in deduped:
            deduped[cid] = dict(course)
            order.append(cid)
        else:
            deduped[cid] = _merge_scraped_course(deduped[cid], course)

    return [deduped[cid] for cid in order]


def _is_course_snapshot_finished(course: dict, now: Optional[datetime] = None) -> bool:
    now = now or business_now()
    end_time = parse_datetime(course.get("end_time", ""))
    return bool(end_time and end_time <= now)


def _drop_finished_course_snapshots(courses: List[dict]) -> List[dict]:
    now = business_now()
    active_courses = [course for course in courses if not _is_course_snapshot_finished(course, now)]
    skipped = len(courses) - len(active_courses)
    if skipped:
        logger.info(f"跳过 {skipped} 门已结束课程快照")
    return active_courses


def _is_course_select_url(url: str) -> bool:
    return "/system/course-select" in (url or "")


async def _refresh_course_list(page: Page) -> bool:
    if await _click_visible_text_control(page, ["刷新列表", "刷新"]):
        await _wait_course_tables_ready(page)
        logger.info("Refreshed course list via explicit refresh control")
        return True

    try:
        await page.reload(wait_until="networkidle", timeout=20000)
        await page.wait_for_timeout(1200)
        logger.info("Reloaded current course-select page")
        return True
    except Exception as e:
        logger.debug(f"Refresh course list failed: {e}")
        return False


async def _open_course_select_page(page: Page) -> bool:
    if _is_course_select_url(page.url):
        logger.info(f"复用当前选课页: {safe_url_for_log(page.url)}")
        if not await _ensure_session_with_retry(page, "复用选课页"):
            return False
        await _refresh_course_list(page)
        return await _wait_course_tables_ready(page)

    try:
        logger.info(f"直接导航到选课页: {BYKC_COURSE_URL}")
        await page.goto(BYKC_COURSE_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        if not await _ensure_session_with_retry(page, "直达选课页后"):
            return False
        if await _wait_course_tables_ready(page):
            return True
    except Exception as e:
        logger.warning(f"直接进入选课页失败，回退到首页菜单导航: {e}")

    logger.info(f"导航到博雅首页: {BYKC_HOME_URL}")
    await page.goto(BYKC_HOME_URL, wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(3000)

    if not await _ensure_session_with_retry(page, "进入首页后"):
        logger.error("会话恢复失败")
        return False

    logger.info("展开「我的课程」菜单...")
    try:
        my_course_menu = page.locator('li:has-text("我的课程"), span:has-text("我的课程")')
        if await my_course_menu.count() > 0:
            await my_course_menu.first.click()
            await page.wait_for_timeout(1500)
            logger.info("已点击「我的课程」")
    except Exception as e:
        logger.warning(f"展开菜单失败: {e}")

    logger.info("点击「选择课程」...")
    try:
        select_menu = page.locator('li[href="/system/course-select"], a[href="/system/course-select"]')
        if await select_menu.count() == 0:
            select_menu = page.locator('li:has-text("选择课程"):visible, span:has-text("选择课程"):visible')

        if await select_menu.count() > 0:
            await select_menu.first.click()
            await page.wait_for_timeout(5000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            logger.info(f"已点击「选择课程」，当前 URL: {safe_url_for_log(page.url)}")
        else:
            logger.warning("未找到「选择课程」菜单项")
            return False
    except Exception as e:
        logger.warning(f"点击选择课程失败: {e}")
        return False

    return await _wait_course_tables_ready(page)


async def _scrape_courses_impl(page: Page, include_details: bool = True) -> List[dict]:
    """
    从博雅选课页面抓取课程信息
    """
    courses = []
    response_recorder = _JsonResponseRecorder(page)

    try:
        os.makedirs("logs", exist_ok=True)
        response_recorder.start()
        if not await _open_course_select_page(page):
            logger.error("无法进入选择课程页面")
            raise ScrapeNavigationError("无法进入选择课程页面")

        # 截图
        await page.screenshot(path="logs/scrape_page.png", full_page=True)
        logger.info("选课页面截图已保存")

        if await _course_page_has_empty_state(page):
            logger.info("当前选课窗口没有可选课程，按空状态完成本轮抓取")
            return []

        # 等待课程表格加载
        try:
            await page.wait_for_selector("table:visible", timeout=COURSE_PAGE_READY_TIMEOUT_MS)
            logger.info("课程表格已加载")
        except Exception:
            logger.warning("等待表格超时，保存 HTML 用于调试...")
            html = await page.content()
            with open("logs/scrape_page.json", "w", encoding="utf-8") as f:
                f.write(html)
            raise ScrapePageNotReadyError("课程列表未在限定时间内出现")

        await _reset_course_filters(page)

        scraped_any_view = False
        for view_key, aliases in COURSE_VIEW_CANDIDATES:
            activated = await _activate_course_view(page, aliases)
            if not activated:
                if view_key == "all" and not scraped_any_view:
                    logger.info("未找到“全部课程”入口，回退到当前默认视图抓取")
                else:
                    logger.info(f"视图[{view_key}] 未找到可切换入口，跳过")
                    continue

            await _reset_course_filters(page)
            await _go_to_first_page(page)
            view_courses = await _collect_current_view_courses(page, include_details, view_key if activated else "default")
            if view_courses:
                scraped_any_view = True
            courses.extend(view_courses)

        if not scraped_any_view:
            logger.warning("未成功切换到任何显式课程视图，尝试抓取当前页面默认视图")
            await _reset_course_filters(page)
            await _go_to_first_page(page)
            current_view_courses = await _collect_current_view_courses(page, include_details, "default-fallback")
            courses.extend(current_view_courses)

    except Exception as e:
        logger.error(f"抓取课程列表失败: {e}")
        try:
            await page.screenshot(path="logs/scrape_error.png", full_page=True)
        except Exception:
            pass
        raise
    finally:
        await response_recorder.stop()

    network_courses = response_recorder.extract_courses()
    if network_courses:
        logger.info(f"网络层补充 {len(network_courses)} 条课程")
        courses = list(network_courses) + courses
    deduped_courses = _dedupe_scraped_courses(courses)
    deduped_courses = _drop_finished_course_snapshots(deduped_courses)
    logger.info(f"共抓取到 {len(courses)} 条课程，去重后 {len(deduped_courses)} 条")
    return deduped_courses


def _classify_scrape_exception(page: Page, error: Exception) -> tuple[ScrapeStatus, str]:
    """把技术异常映射为稳定的业务状态，避免上层依赖异常文本。"""

    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        pass

    if _is_sso_login_page(current_url):
        return ScrapeStatus.AUTH_EXPIRED, "课程系统登录状态已失效"
    if isinstance(error, ScrapeNavigationError):
        return ScrapeStatus.UPSTREAM_UNAVAILABLE, "课程系统页面无法打开"
    if isinstance(error, ScrapePageNotReadyError):
        return ScrapeStatus.PARSE_FAILED, "课程页面结构未按预期加载"
    if isinstance(error, (asyncio.TimeoutError, PlaywrightTimeoutError)):
        return ScrapeStatus.TIMEOUT, "课程系统响应超时"

    error_text = str(error).lower()
    if "timeout" in error_text or "timed out" in error_text:
        return ScrapeStatus.TIMEOUT, "课程系统响应超时"
    return ScrapeStatus.PARSE_FAILED, "课程页面解析失败"


async def scrape_courses_result(page: Page, include_details: bool = True) -> ScrapeOutcome:
    """执行一次抓取并返回结构化结果。

    这里不把失败降级为空列表；只有页面明确呈现空状态时才返回
    ``SUCCESS_EMPTY``，从而阻止错误快照覆盖数据库或触发误通知。
    """

    try:
        courses = await _scrape_courses_impl(page, include_details=include_details)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        status, message = _classify_scrape_exception(page, error)
        logger.error(
            f"课程抓取结果: status={status.value}, exception={type(error).__name__}"
        )
        return ScrapeOutcome(
            status=status,
            message=message,
            metadata={"exception_type": type(error).__name__},
        )

    if courses:
        return ScrapeOutcome(
            status=ScrapeStatus.SUCCESS_WITH_COURSES,
            courses=courses,
            message=f"抓取到 {len(courses)} 门课程",
        )
    return ScrapeOutcome(
        status=ScrapeStatus.SUCCESS_EMPTY,
        message="当前选课窗口没有可选课程",
    )


async def scrape_courses(page: Page, include_details: bool = True) -> List[dict]:
    """兼容旧调用方的列表接口；新代码应使用 :func:`scrape_courses_result`。"""

    outcome = await scrape_courses_result(page, include_details=include_details)
    if not outcome.success:
        logger.error(f"兼容列表接口收到失败结果: status={outcome.status.value}")
    return outcome.courses


async def _enrich_with_details(page: Page, courses: List[dict]) -> List[dict]:
    """
    点击每门课的「详细介绍」获取详情页信息（签到方式、课程介绍等）
    """
    logger.info(f"开始抓取 {len(courses)} 门课程的详情...")

    for i, course in enumerate(courses):
        try:
            row_index = course.get("__row_index")
            table_index = course.get("__table_index", 0)
            if row_index is None:
                logger.warning(f"课程[{i}] 缺少行索引，跳过详情抓取")
                continue

            visible_tables = page.locator("table:visible")
            row = visible_tables.nth(table_index).locator("tbody tr").nth(row_index)
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
            await page.wait_for_selector("table:visible", timeout=15000)

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
        course.pop("__table_index", None)
    return courses


async def _parse_visible_course_tables(page: Page) -> List[dict]:
    """Parse all visible course tables on the current page."""
    fast_path_courses = await _extract_visible_course_rows_via_dom(page)
    tables = await _get_visible_course_tables(page)
    if fast_path_courses:
        visible_row_budget = 0
        for _, table in tables:
            try:
                visible_row_budget += await table.locator("tbody tr").count()
            except Exception:
                continue

        minimum_complete_rows = max(
            MIN_FAST_PATH_ROWS,
            int(visible_row_budget * 0.8) if visible_row_budget else 0,
        )
        if len(fast_path_courses) >= minimum_complete_rows:
            return _dedupe_scraped_courses(fast_path_courses)

        logger.info(
            f"DOM 快路径仅抓到 {len(fast_path_courses)} / {visible_row_budget or '?'} 条课程，补跑 Locator 兜底"
        )

    if not tables:
        logger.warning("当前页面未找到可见课程表格")
        return _dedupe_scraped_courses(fast_path_courses)

    courses = list(fast_path_courses)
    for table_index, table in tables:
        courses.extend(await _parse_course_table(table, table_index))
    return _dedupe_scraped_courses(courses)


async def _parse_course_table(table: Locator, table_index: int) -> List[dict]:
    """解析单个课程表格"""
    courses = []
    column_map = await _build_table_column_map(table)
    rows = table.locator("tbody tr")
    row_count = await rows.count()

    for row_index in range(row_count):
        try:
            row = rows.nth(row_index)
            cells = row.locator("td")
            cell_count = await cells.count()
            if cell_count < 6:
                continue

            # 提取各列文本
            cell_texts = []
            for cell_idx in range(cell_count):
                cell = cells.nth(cell_idx)
                text = await cell.inner_text()
                cell_texts.append(text.strip())
            course_data = _build_course_row_payload(cell_texts, column_map, row_index, table_index)
            if course_data:
                courses.append(course_data)
            continue

            # 优先根据表头识别列，识别失败时再回退到旧索引。
            status_text = _get_cell_text(cell_texts, column_map, "status", 0)
            name = _get_cell_text(cell_texts, column_map, "name", 1)
            category = _get_cell_text(cell_texts, column_map, "category", 2)

            # 课程信息列：包含地点、教师、学院等（多行）
            course_info = _get_cell_text(cell_texts, column_map, "info", 3)
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
            time_info = _get_cell_text(cell_texts, column_map, "time", 4)
            start_time_str = ""
            end_time_str = ""
            for line in time_info.split("\n"):
                line = line.strip()
                if "开始" in line:
                    # 只按第一个中文冒号分割，保留后面的时间如 "2026-03-04 19:00"
                    start_time_str = line.split("：", 1)[-1].strip() if "：" in line else line.split(":", 1)[-1].strip() if ":" in line else line
                elif "结束" in line:
                    end_time_str = line.split("：", 1)[-1].strip() if "：" in line else line.split(":", 1)[-1].strip() if ":" in line else line
            if not start_time_str or not end_time_str:
                dt_tokens = _extract_datetime_tokens(time_info)
                if dt_tokens:
                    start_time_str = start_time_str or dt_tokens[0]
                    if len(dt_tokens) > 1:
                        end_time_str = end_time_str or dt_tokens[1]

            # 开放群体列：校区、学院、年级等
            group_info = _get_cell_text(cell_texts, column_map, "group", 5)
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
            enroll_info = _get_cell_text(cell_texts, column_map, "enroll", 6)
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
            if not enroll_start_str or not enroll_end_str:
                dt_tokens = _extract_datetime_tokens(enroll_info)
                if dt_tokens:
                    enroll_start_str = enroll_start_str or dt_tokens[0]
                    if len(dt_tokens) > 1:
                        enroll_end_str = enroll_end_str or dt_tokens[1]

            # 课程作业
            has_homework = _get_cell_text(cell_texts, column_map, "homework", 7)

            # 课程人数
            capacity_text = _get_cell_text(cell_texts, column_map, "capacity", 8)
            enrolled, capacity = parse_capacity(capacity_text)

            course_id = generate_course_id(
                name,
                start_time_str,
                enroll_start_str,
                teacher,
                location,
                campus,
            )
            legacy_course_id = generate_legacy_course_id(name, start_time_str, enroll_start_str, teacher)

            course_data = {
                "id": course_id,
                "legacy_id": legacy_course_id,
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
                "__table_index": table_index,
            }
            courses.append(course_data)

        except Exception as e:
            logger.warning(f"解析课程行失败: {e}")
            continue

    return courses


async def _go_to_next_page(page: Page) -> bool:
    """尝试翻到下一页，返回是否成功"""
    try:
        before_signature = await _capture_visible_course_table_signature(page)

        # 截图中可见「上一页」「1」「下一页」按钮
        next_btn = page.locator(
            'a:has-text("下一页"), '
            'button:has-text("下一页"), '
            'li:has-text("下一页") a'
        )
        count = await next_btn.count()
        for idx in range(count):
            candidate = next_btn.nth(idx)
            if not await candidate.is_visible():
                continue
            classes = (await candidate.get_attribute("class") or "").lower()
            aria_disabled = (await candidate.get_attribute("aria-disabled") or "").lower() == "true"
            disabled_attr = await candidate.get_attribute("disabled") is not None
            is_disabled = await candidate.is_disabled() or aria_disabled or disabled_attr or "disabled" in classes
            if not is_disabled:
                await candidate.click()
                await page.wait_for_timeout(3000)
                await page.wait_for_load_state("networkidle", timeout=10000)
                after_signature = await _capture_visible_course_table_signature(page)
                if after_signature == before_signature:
                    logger.warning("下一页点击后课程表格没有变化，停止翻页以避免死循环")
                    return False
                logger.info("已翻到下一页")
                return True
            logger.info("已到最后一页")
    except Exception as e:
        logger.debug(f"翻页操作: {e}")
    return False


def assess_scrape_health(courses_data: List[dict]) -> dict:
    """Check whether the current scrape snapshot is plausible enough to write."""
    now = business_now()
    session = get_session()
    try:
        db_active_count = (
            session.query(Course)
            .filter(Course.expired == False)  # noqa: E712
            .count()
        )
    finally:
        session.close()

    scraped_count = len(courses_data)
    future_or_open_count = 0
    available_count = 0
    preview_count = 0
    for course in courses_data:
        enroll_end = parse_datetime(course.get("enroll_end", ""))
        enroll_start = parse_datetime(course.get("enroll_start", ""))
        if not enroll_end or enroll_end >= now:
            future_or_open_count += 1
        remaining = max(0, int(course.get("capacity") or 0) - int(course.get("enrolled") or 0))
        if remaining > 0:
            available_count += 1
        status_text = (course.get("status") or "").strip()
        if "预告" in status_text or "未开" in status_text or (enroll_start and enroll_start > now):
            preview_count += 1

    minimum_expected = max(
        SCRAPE_HEALTH_MIN_ROWS,
        int(db_active_count * SCRAPE_HEALTH_RATIO) if db_active_count else 0,
    )
    suspiciously_low = (
        db_active_count >= SCRAPE_HEALTH_MIN_BASELINE
        and scraped_count < minimum_expected
        and scraped_count < db_active_count - 3
    )

    return {
        "healthy": not suspiciously_low,
        "db_active_count": db_active_count,
        "scraped_count": scraped_count,
        "future_or_open_count": future_or_open_count,
        "available_count": available_count,
        "preview_count": preview_count,
        "minimum_expected": minimum_expected,
    }


def save_courses_to_db(courses_data: List[dict]) -> List[str]:
    """
    将抓取到的课程保存到数据库，返回新发现课程的 ID 列表。
    同时检测退课捡漏（之前满→现在有名额），保存到全局 _reopened_course_ids。
    """
    global _reopened_course_ids
    session = get_session()
    new_course_ids = []
    _reopened_course_ids = []
    reused_similar_rows = 0
    skipped_finished_rows = 0

    try:
        now = business_now()
        lookup_ids = list(dict.fromkeys(
            [data["id"] for data in courses_data]
            + [
                data["legacy_id"]
                for data in courses_data
                if data.get("legacy_id") and data["legacy_id"] != data["id"]
            ]
        ))
        existing_by_id = {}
        for offset in range(0, len(lookup_ids), 500):
            rows = (
                session.query(Course)
                .filter(Course.id.in_(lookup_ids[offset:offset + 500]))
                .all()
            )
            existing_by_id.update({course.id: course for course in rows})

        for data in courses_data:
            existing = existing_by_id.get(data["id"])
            if not existing:
                legacy_id = data.get("legacy_id")
                if legacy_id and legacy_id != data["id"]:
                    legacy_existing = existing_by_id.get(legacy_id)
                    if legacy_existing and _legacy_identity_matches(legacy_existing, data):
                        existing = legacy_existing
            if not existing:
                existing = _find_similar_active_course(session, data, now)
                if existing:
                    reused_similar_rows += 1
            if existing:
                # Also protects against duplicate rows in a single scraped snapshot.
                existing_by_id[data["id"]] = existing

            start_time_dt = parse_datetime(data.get("start_time", ""))
            enroll_start_dt = parse_datetime(data.get("enroll_start", ""))
            end_time_dt = parse_datetime(data.get("end_time", ""))
            enroll_end_dt = parse_datetime(data.get("enroll_end", ""))
            has_course_ended = bool(end_time_dt and end_time_dt <= now)
            is_expired = bool(has_course_ended or (enroll_end_dt and enroll_end_dt <= now))

            if has_course_ended and not existing:
                skipped_finished_rows += 1
                continue

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
                existing.start_time = start_time_dt or existing.start_time
                existing.end_time = end_time_dt or existing.end_time
                existing.enroll_start = enroll_start_dt or existing.enroll_start
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
                    start_time=start_time_dt,
                    end_time=end_time_dt,
                    enroll_start=enroll_start_dt,
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
                    first_seen=now,
                    last_seen=now,
                    pushed=False,
                    expired=is_expired,
                )
                session.add(course)
                existing_by_id[data["id"]] = course
                new_course_ids.append(data["id"])

        _cleanup_near_duplicate_courses(session, now)
        session.commit()
        extra = f", {len(_reopened_course_ids)} 门退课捡漏" if _reopened_course_ids else ""
        similar_extra = f", {reused_similar_rows} 条近重复记录复用旧课程" if reused_similar_rows else ""
        skipped_extra = f", 跳过 {skipped_finished_rows} 条已结束课程" if skipped_finished_rows else ""
        logger.info(f"数据库更新完成: {len(new_course_ids)} 条新课程, "
                     f"{len(courses_data) - len(new_course_ids) - skipped_finished_rows} 条已有课程已更新{similar_extra}{extra}{skipped_extra}")
    except Exception as e:
        session.rollback()
        logger.error(f"保存课程到数据库失败: {e}")
        raise
    finally:
        session.close()

    return new_course_ids


# 全局变量：退课捡漏课程 ID（由 save_courses_to_db 设置, run_scrape_task 消费）
_reopened_course_ids = []
