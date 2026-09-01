"""时间处理工具。

数据库目前保存的是不带时区的时间，因此这里仍返回不带时区的
``datetime``，但其取值统一来自明确配置的业务时区，而不是服务器默认时区。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "Asia/Shanghai"


def get_business_timezone() -> ZoneInfo:
    """返回业务时区；配置错误时回退到 UTC，避免启动时依赖服务器时区。"""
    configured = (os.getenv("APP_TIMEZONE") or DEFAULT_TIMEZONE).strip()
    try:
        return ZoneInfo(configured)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def now() -> datetime:
    """返回业务时区的当前时间，并保持 SQLite 旧字段所需的 naive 形式。"""
    return datetime.now(get_business_timezone()).replace(tzinfo=None)


def utc_now() -> datetime:
    """返回带 UTC 时区信息的当前时间，供 feed 等外部格式使用。"""
    return datetime.now(timezone.utc)


def to_utc(value: datetime | None) -> datetime | None:
    """将数据库中的业务时区 naive 时间转换为带时区的 UTC 时间。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=get_business_timezone())
    return value.astimezone(timezone.utc)
