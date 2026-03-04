"""
过滤引擎 - 根据用户配置的筛选条件过滤课程
"""

from datetime import datetime
from typing import List, Tuple
from loguru import logger

from src.models import Course, FilterConfig, get_session


def _is_self_sign_course(course: Course) -> bool:
    """判断课程是否为自主签到/自选类型"""
    check_in = getattr(course, "check_in_method", "") or ""
    sign = getattr(course, "sign_method", "") or ""
    text = f"{check_in} {sign}"
    return ("自主" in text) or ("自选" in text)


def load_filter_config() -> FilterConfig:
    """从数据库加载筛选配置"""
    session = get_session()
    try:
        config = session.query(FilterConfig).first()
        if not config:
            config = FilterConfig(id=1)
            session.add(config)
            session.commit()
        # 拷贝数据以免 session 关闭后无法访问
        session.expunge(config)
        return config
    finally:
        session.close()


def filter_courses(courses: List[Course], config: FilterConfig = None) -> List[Tuple[Course, int]]:
    """
    根据配置过滤课程列表

    Args:
        courses: 待过滤的课程列表
        config: 筛选配置，None 则从数据库加载

    Returns:
        (课程, 优先级分数) 的列表，按分数降序排列
    """
    if config is None:
        config = load_filter_config()

    results = []

    for course in courses:
        score = 0
        passed = True

        # 1. 类别过滤
        if config.categories:
            if course.category not in config.categories:
                passed = False
                continue

        # 2. 自主签到过滤（签到方式来自详情页的 check_in_method）
        if config.self_sign_only:
            if not _is_self_sign_course(course):
                passed = False
                continue
            score += 10  # 自主签到加分

        # 2.5 严格博雅规则：必须自主签到，且非校医院开课
        if getattr(config, "strict_boya_only", False):
            if not _is_self_sign_course(course):
                passed = False
                continue
            organizer = getattr(course, "organizer", "") or ""
            if "校医院" in organizer:
                passed = False
                continue
            score += 10

        # 3. 剩余名额过滤
        if course.remaining < config.min_remaining:
            passed = False
            continue

        # 4. 选课时间过滤（只推送选课未截止的）
        now = datetime.now()
        if course.enroll_end and course.enroll_end < now:
            passed = False
            continue

        # 5. 校区过滤
        if config.campus_filter:
            if config.campus_filter not in course.campus:
                passed = False
                continue

        # 6. 关键词白名单（有白名单时，课程名/类别必须包含至少一个）
        if config.keyword_whitelist:
            matched = any(
                kw in course.name or kw in course.category
                for kw in config.keyword_whitelist
            )
            if not matched:
                passed = False
                continue

        # 7. 关键词黑名单
        if config.keyword_blacklist:
            blocked = any(
                kw in course.name or kw in course.category
                for kw in config.keyword_blacklist
            )
            if blocked:
                passed = False
                continue

        # 8. 优先级关键词计分
        if config.priority_keywords:
            for i, kw in enumerate(config.priority_keywords):
                if kw in course.name or kw in course.category:
                    # 排序越靠前分数越高
                    score += (len(config.priority_keywords) - i) * 5

        # 9. 剩余名额越多分数越高（鼓励报名容易选上的）
        score += min(course.remaining, 50)

        # 10. 选课窗口即将开始加分
        if course.enroll_start:
            if course.enroll_start > now:
                # 即将开始选课，优先级高
                score += 20

        if passed:
            results.append((course, score))

    # 按分数降序排列
    results.sort(key=lambda x: x[1], reverse=True)

    logger.info(f"过滤结果: {len(courses)} 条课程 -> {len(results)} 条通过过滤")
    return results


def get_auto_enroll_candidates(courses: List[Course], config: FilterConfig = None) -> List[Course]:
    """
    获取自动选课候选课程

    Args:
        courses: 已通过过滤的课程列表
        config: 筛选配置

    Returns:
        符合自动选课条件的课程列表（按优先级排序）
    """
    if config is None:
        config = load_filter_config()

    if not config.auto_enroll_enabled:
        return []

    if not config.priority_keywords:
        logger.info("未设置意愿优先级关键词，跳过自动选课")
        return []

    candidates = []
    for course in courses:
        # 必须在选课窗口内
        if not course.is_enrollable:
            continue

        # 必须匹配优先级关键词
        matched = any(
            kw in course.name or kw in course.category
            for kw in config.priority_keywords
        )
        if matched:
            candidates.append(course)

    # 按优先级关键词顺序排序
    def priority_sort_key(c):
        for i, kw in enumerate(config.priority_keywords):
            if kw in c.name or kw in c.category:
                return i
        return len(config.priority_keywords)

    candidates.sort(key=priority_sort_key)
    return candidates[:config.max_auto_enroll_per_day]
