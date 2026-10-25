"""Course pager navigation, independent from parsing, persistence and delivery."""

from dataclasses import dataclass
import re
from typing import Awaitable, Callable, Iterable

from loguru import logger

from src.scrape_outcome import ScrapePageNotReadyError


def course_page_identity(courses: Iterable[dict]) -> tuple[str, ...]:
    """Page movement depends on course identities, never changing seat counts."""
    return tuple(sorted({str(row["id"]) for row in courses if row.get("id")}))


async def control_disabled(candidate) -> bool:
    return await candidate.evaluate("""el => {
      for (let node = el; node && node !== document.body; node = node.parentElement) {
        if (node.disabled || node.hasAttribute('disabled') ||
            node.getAttribute('aria-disabled') === 'true' ||
            /(^|[\\s_-])disabled($|[\\s_-])/.test(String(node.className || ''))) return true;
      }
      return false;
    }""")


async def numeric_boundary(candidate, *, forward: bool) -> bool:
    return await candidate.evaluate("""(el, forward) => {
      const root = el.closest('.pagination, .el-pagination, .ant-pagination');
      if (!root) return false;
      const numeric = Array.from(root.querySelectorAll('li')).map(node => ({
        value: /^\\d+$/.test(node.textContent.trim()) ? Number(node.textContent.trim()) : null,
        active: /(^|\\s)(active|current|selected|is-active)(\\s|$)/.test(String(node.className || '')) ||
          node.getAttribute('aria-current') === 'page' || !!node.querySelector('[aria-current="page"]')
      })).filter(item => item.value !== null);
      const active = numeric.filter(item => item.active);
      if (active.length !== 1) return false;
      if (!forward) return active[0].value === 1;
      if (/[.…]/.test(root.textContent) || root.querySelector('.ant-pagination-jump-next')) return false;
      const pages = [...new Set(numeric.map(item => item.value))].sort((a,b) => a-b);
      return pages.length > 0 && pages.every((value,index) => value === index+1) && active[0].value === pages.length;
    }""", forward)


@dataclass
class CoursePager:
    page: object
    read_identity: Callable[[], Awaitable[tuple[str, ...]]]
    wait_ready: Callable[[], Awaitable[bool]]
    wait_idle: Callable[[], Awaitable[bool]]
    poll_count: int = 25
    poll_ms: int = 200

    async def endpoint(self, *, first: bool) -> bool:
        label = "首页|第一页" if first else "末页|最后一页"
        controls = self.page.locator(
            '.pagination a, .pagination button, .el-pagination a, .ant-pagination a'
        ).filter(has_text=re.compile(f"^\\s*(?:{label})\\s*$"))
        for index in range(await controls.count()):
            candidate = controls.nth(index)
            if not await candidate.is_visible():
                continue
            if not await control_disabled(candidate):
                # Only read-only pager controls use native clicks through overlays.
                await candidate.evaluate("el => el.click()")
                if not await self.wait_idle() or not await self.wait_ready():
                    raise ScrapePageNotReadyError("分页端点请求未稳定，拒绝不完整快照")
                # Legacy endpoints have no marker and are idempotent at the edge.
                await self.page.wait_for_timeout(500)
            return True
        return False

    async def first(self, *, max_pages: int) -> bool:
        if await self.endpoint(first=True):
            return True
        for _ in range(max_pages):
            if not await self.move(forward=False):
                return True
        raise ScrapePageNotReadyError("无法确认已回到第一页，拒绝不完整快照")

    async def move(self, *, forward: bool) -> bool:
        try:
            before = await self.read_identity()
            if not before:
                raise ScrapePageNotReadyError("翻页前课程身份未知，拒绝不完整快照")
            label, short = ("下一页", "下页") if forward else ("上一页", "上页")
            extra = ('.el-pagination .btn-next, .ant-pagination-next button, .ant-pagination-next a'
                     if forward else '.el-pagination .btn-prev, .ant-pagination-prev button, .ant-pagination-prev a')
            selectors = [extra]
            for root in ('.pagination', '.el-pagination', '.ant-pagination'):
                selectors += [f'{root} a:has-text("{label}")', f'{root} button:has-text("{label}")',
                              f'{root} a:text-is("{short}")', f'{root} button:text-is("{short}")',
                              f'{root} [aria-label="{label}"]']
            controls = self.page.locator(', '.join(selectors))
            for index in range(await controls.count()):
                candidate = controls.nth(index)
                if not await candidate.is_visible():
                    continue
                if await control_disabled(candidate) or await numeric_boundary(candidate, forward=forward):
                    return False
                await candidate.evaluate("el => el.click()")
                await self.wait_idle()
                await self.wait_ready()
                for _ in range(self.poll_count):
                    after = await self.read_identity()
                    if after and after != before:
                        logger.info("课程分页完成: direction={}", "next" if forward else "previous")
                        return True
                    await self.page.wait_for_timeout(self.poll_ms)
                # A value change on the same page is not movement. Use an
                # independent endpoint, never infer an end from unchanged IDs alone.
                if await self.endpoint(first=not forward) and await self.read_identity() == before:
                    logger.info("Course pagination boundary: explicit {} control confirmed course identities", "last" if forward else "first")
                    return False
                raise ScrapePageNotReadyError(f"{label}未更新且无法确认分页边界，拒绝不完整快照")
            return False
        except ScrapePageNotReadyError:
            raise
        except Exception as error:
            raise ScrapePageNotReadyError("翻页失败，拒绝不完整快照") from error
