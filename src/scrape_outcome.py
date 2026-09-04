"""课程抓取结果契约。

抓取层必须区分“页面正常且当前无课”和“抓取过程失败”。
该模块提供稳定、可序列化的结果类型，供调度器、接口和后续通知流水线复用。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class ScrapeStatus(str, Enum):
    """一次课程抓取的业务结果。"""

    SUCCESS_WITH_COURSES = "success_with_courses"
    SUCCESS_EMPTY = "success_empty"
    AUTH_EXPIRED = "auth_expired"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    PARSE_FAILED = "parse_failed"
    TIMEOUT = "timeout"


class ScrapeFailure(RuntimeError):
    """可被抓取结果层分类的失败基类。"""


class ScrapeNavigationError(ScrapeFailure):
    """无法进入或恢复课程页面。"""


class ScrapePageNotReadyError(ScrapeFailure):
    """课程页面已打开，但列表没有按预期加载。"""


@dataclass
class ScrapeOutcome:
    """课程抓取的结构化结果。

    ``courses`` 只在成功结果中作为业务快照使用；失败结果仍保留为空列表，
    避免调用方误把部分数据当成完整快照。
    """

    status: ScrapeStatus | str
    courses: List[dict] = field(default_factory=list)
    message: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.status = ScrapeStatus(self.status)

    @property
    def success(self) -> bool:
        return self.status in {
            ScrapeStatus.SUCCESS_WITH_COURSES,
            ScrapeStatus.SUCCESS_EMPTY,
        }

    @property
    def is_empty(self) -> bool:
        return self.status == ScrapeStatus.SUCCESS_EMPTY

    def to_dict(self) -> Dict[str, Any]:
        """返回可直接用于日志、接口或报告的字典。"""

        return {
            "status": self.status.value,
            "success": self.success,
            "scraped_count": len(self.courses) if self.success else 0,
            "message": self.message,
            "metadata": dict(self.metadata),
        }
