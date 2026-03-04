"""
RSS Feed 生成模块
提供标准 RSS 2.0 / Atom Feed 端点
"""

from datetime import datetime, timezone
from typing import List
from loguru import logger

try:
    from feedgen.feed import FeedGenerator
    HAS_FEEDGEN = True
except ImportError:
    HAS_FEEDGEN = False
    logger.warning("feedgen 未安装，RSS 功能不可用")


def generate_rss_feed(courses: list, base_url: str = "http://localhost:5000") -> str:
    """
    生成 RSS XML 字符串

    Args:
        courses: Course 对象列表
        base_url: 服务器基础 URL

    Returns:
        RSS XML 字符串
    """
    if not HAS_FEEDGEN:
        return "<rss><channel><title>feedgen not installed</title></channel></rss>"

    fg = FeedGenerator()
    fg.title("BUAA 博雅课程推送")
    fg.link(href=f"{base_url}/rss", rel="self")
    fg.link(href="https://bykc.buaa.edu.cn/system/course-select", rel="alternate")
    fg.description("BUAA 博雅素质课程自动推送 - 新课程通知")
    fg.language("zh-CN")
    fg.lastBuildDate(datetime.now(timezone.utc))

    for course in courses:
        fe = fg.add_entry()
        fe.id(course.id)
        fe.title(f"[{course.category}] {course.name}")

        # 签到方式标记
        sign_icon = "✅ 自主签课" if "自主" in course.sign_method else "⚠️ " + course.sign_method

        # 构建详情
        description = f"""
        <h3>{course.name}</h3>
        <ul>
            <li><strong>类别:</strong> {course.category}</li>
            <li><strong>教师:</strong> {course.teacher}</li>
            <li><strong>地点:</strong> {course.location}</li>
            <li><strong>校区:</strong> {course.campus}</li>
            <li><strong>课程时间:</strong>
                {course.start_time.strftime('%Y-%m-%d %H:%M') if course.start_time else '未知'}
                ~ {course.end_time.strftime('%Y-%m-%d %H:%M') if course.end_time else '未知'}</li>
            <li><strong>选课时间:</strong>
                {course.enroll_start.strftime('%Y-%m-%d %H:%M') if course.enroll_start else '未知'}
                ~ {course.enroll_end.strftime('%Y-%m-%d %H:%M') if course.enroll_end else '未知'}</li>
            <li><strong>签到方式:</strong> {sign_icon}</li>
            <li><strong>名额:</strong> {course.enrolled}/{course.capacity} (剩余 {course.remaining})</li>
        </ul>
        """
        fe.description(description)
        fe.link(href="https://bykc.buaa.edu.cn/system/course-select")

        # 使用 first_seen 作为发布时间
        if course.first_seen:
            fe.published(course.first_seen.replace(tzinfo=timezone.utc))

    return fg.rss_str(pretty=True).decode("utf-8")


def generate_atom_feed(courses: list, base_url: str = "http://localhost:5000") -> str:
    """生成 Atom Feed XML 字符串"""
    if not HAS_FEEDGEN:
        return "<feed><title>feedgen not installed</title></feed>"

    fg = FeedGenerator()
    fg.title("BUAA 博雅课程推送")
    fg.link(href=f"{base_url}/atom", rel="self")
    fg.link(href="https://bykc.buaa.edu.cn/system/course-select", rel="alternate")
    fg.subtitle("BUAA 博雅素质课程自动推送")
    fg.language("zh-CN")
    fg.id(f"{base_url}/atom")

    for course in courses:
        fe = fg.add_entry()
        fe.id(course.id)
        fe.title(f"[{course.category}] {course.name}")
        fe.summary(
            f"{course.name} | {course.teacher} | {course.location} | "
            f"{course.sign_method} | 剩余 {course.remaining} 人"
        )
        fe.link(href="https://bykc.buaa.edu.cn/system/course-select")
        if course.first_seen:
            fe.published(course.first_seen.replace(tzinfo=timezone.utc))
            fe.updated(course.first_seen.replace(tzinfo=timezone.utc))

    return fg.atom_str(pretty=True).decode("utf-8")
