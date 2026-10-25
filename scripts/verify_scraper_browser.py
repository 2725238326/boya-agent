"""Exercise actual scraper pagination against local HTML, never campus services."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import scraper
from src.scrape_outcome import ScrapePageNotReadyError


def fixture():
    headers = ["状态", "课程名称", "课程类别", "检索信息", "课程时间", "全局检索", "选课信息", "作业", "人数", "操作"]
    def row(name, enrolled):
        cells = ["可报名", name, "博雅课程-文化", "地点：教室<br>教师：老师<br>学院：学院",
                 "开始：2099-10-25 14:00<br>结束：2099-10-25 16:00", "校区：学院路",
                 "选课方式：直接选课<br>选课开始：2099-10-24 12:00<br>选课结束：2099-10-25 12:00",
                 "无作业", f"{enrolled}/20", "详细介绍"]
        return "<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>"
    shared = row("相同开头" * 70, 10)
    first = shared + row("第一页独有", 19)
    second = shared + row("第二页独有", 20)
    return ("<table><thead><tr>" + "".join(f"<th>{name}</th>" for name in headers) +
        f"</tr></thead><tbody>{first}</tbody></table>" + """
        <ul class="pagination"><li class="disabled" id="prev"><a href="#">上一页</a></li>
        <li id="next"><a href="#">下一页</a></li></ul>
        <script>
        const pages = [FIRST, SECOND]; let current = 0; let clicks = 0;
        function move(delta) {
          clicks++;
          if (window.stuck) return;
          setTimeout(() => {
            current = Math.max(0, Math.min(1, current + delta));
            document.querySelector('tbody').innerHTML = pages[current];
            document.querySelector('#prev').className = current === 0 ? 'disabled' : '';
            document.querySelector('#next').className = current === 1 ? 'disabled' : '';
          }, 250);
        }
        document.querySelector('#prev a').onclick = e => { e.preventDefault(); move(-1); };
        document.querySelector('#next a').onclick = e => { e.preventDefault(); move(1); };
        </script>""".replace("FIRST", __import__("json").dumps(first)).replace("SECOND", __import__("json").dumps(second)))


async def legacy_fixture(page, *, changing_seats=False, delayed=False):
    await page.goto('about:blank')
    await page.set_content(fixture())
    await page.evaluate('''({changingSeats, delayed}) => {
        const root = document.querySelector('.pagination');
        root.insertAdjacentHTML('afterbegin','<li><a id="first">首页</a></li>');
        root.insertAdjacentHTML('beforeend','<li><a id="last">末页</a></li>');
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:transparent';
        document.body.append(overlay);
        function show(index) {
          current = index;
          document.querySelector('tbody').innerHTML = pages[index];
          document.querySelector('#prev').className = '';
          document.querySelector('#next').className = '';
        }
        document.querySelector('#first').onclick = () => show(0);
        document.querySelector('#last').onclick = () => {
          if (current !== 1) show(1);
        };
        document.querySelector('#prev a').onclick = e => {e.preventDefault();show(Math.max(0,current-1));};
        document.querySelector('#next a').onclick = e => {
          e.preventDefault();
          if (current === 0) {
            if (delayed) {
              document.querySelector('tbody tr:last-child td:nth-child(9)').textContent = '18/20';
              setTimeout(() => show(1), 900);
            } else show(1);
          } else if (changingSeats) {
            document.querySelector('tbody tr:last-child td:nth-child(9)').textContent = '19/20';
          }
        };
        show(0);
    }''', {'changingSeats':changing_seats, 'delayed':delayed})


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.abort())
            await page.set_content(fixture())
            # Real table extraction, header mapping, delayed JS pagination and
            # ancestor disabled states; only external session checks are mocked.
            with patch.object(scraper, "_ensure_session_with_retry", new=AsyncMock(return_value=True)), patch.object(
                scraper, "_wait_for_network_idle", new=AsyncMock(return_value=True)
            ):
                await scraper._go_to_first_page(page)
                courses = scraper._dedupe_scraped_courses(await scraper._collect_current_view_courses(page, False, "fixture"))
                assert len(courses) == 3
                assert {item["name"] for item in courses} >= {"第一页独有", "第二页独有"}
                assert next(item for item in courses if item["name"] == "第二页独有")["enrolled"] == 20
                assert await page.evaluate("clicks") == 1, "disabled ancestor must not be clicked"
                await scraper._go_to_first_page(page)
                assert await page.evaluate("current") == 0
                await page.evaluate("window.stuck = true")
                try:
                    await scraper._go_to_next_page(page)
                except ScrapePageNotReadyError:
                    pass
                else:
                    raise AssertionError("stuck enabled pager must reject snapshot")
                # Actual campus shape: arrows have no disabled state even at
                # the edge; the active numeric page supplies the boundary.
                await page.goto('about:blank')
                await page.set_content(fixture())
                await page.evaluate('''() => {
                    document.querySelector('#prev').className = '';
                    document.querySelector('#next').insertAdjacentHTML('beforebegin', '<li class="active"><a>1</a></li>');
                }''')
                await scraper._go_to_first_page(page)
                assert not await scraper._go_to_next_page(page)
                assert await page.evaluate('clicks') == 0
                await page.goto('about:blank')
                await page.set_content(fixture())
                await page.evaluate('''() => {
                    document.querySelector('#prev').className = '';
                    const overlay = document.createElement('div');
                    overlay.style.cssText = 'position:fixed;inset:0;z-index:99999;background:transparent';
                    document.body.append(overlay);
                    const root = document.querySelector('.pagination');
                    root.insertAdjacentHTML('afterbegin','<li><a id="first">首页</a></li>');
                    root.insertAdjacentHTML('beforeend','<li><a id="last">末页</a></li>');
                    document.querySelector('#first').onclick = () => move(-2);
                    document.querySelector('#last').onclick = () => move(2);
                    const update = () => {
                        if (document.querySelector('#prev').className) document.querySelector('#prev').className = '';
                        if (document.querySelector('#next').className) document.querySelector('#next').className = '';
                    };
                    new MutationObserver(update).observe(root, {attributes:true,subtree:true,attributeFilter:['class']});
                }''')
                # Avoid an observer loop while preserving the real unmarked-arrow shape.
                await page.evaluate('''() => {
                    document.querySelector('#prev a').onclick = e => {e.preventDefault(); move(-1);};
                }''')
                await scraper._go_to_first_page(page)
                legacy = scraper._dedupe_scraped_courses(await scraper._collect_current_view_courses(page, False, 'legacy'))
                assert len(legacy) == 3
                await scraper._go_to_first_page(page)
                await page.evaluate("() => { document.querySelector('#next a').onclick = e => e.preventDefault(); }")
                try:
                    await scraper._go_to_next_page(page)
                except ScrapePageNotReadyError:
                    pass
                else:
                    raise AssertionError('a moving last-page control must expose a broken next-page control')
                # Counts may change while the legacy final-page arrow remains
                # enabled. Reuse the same session twice to catch alternating failures.
                await legacy_fixture(page, changing_seats=True)
                for _ in range(2):
                    await scraper._go_to_first_page(page)
                    rows = await scraper._collect_current_view_courses(page, False, 'changing-seats')
                    assert len(rows) == 3
                    assert next(row for row in rows if row['name'] == '第二页独有')['enrolled'] == 19
                await legacy_fixture(page, changing_seats=True, delayed=True)
                delayed_rows = await scraper._collect_current_view_courses(page, False, 'seat-change-before-page-render')
                assert {row['name'] for row in delayed_rows} >= {'第一页独有', '第二页独有'}
                # A broken next arrow which only changes values is still not a
                # terminal-page proof if the independent last control moves.
                await legacy_fixture(page)
                await page.evaluate('''() => { document.querySelector('#next a').onclick = e => {
                    e.preventDefault(); document.querySelector('tbody tr:last-child td:nth-child(9)').textContent = '17/20';
                }; }''')
                try:
                    await scraper._go_to_next_page(page)
                except ScrapePageNotReadyError:
                    pass
                else:
                    raise AssertionError('seat changes must not conceal a broken next arrow')
                # Legacy endpoint requests can finish before deferred DOM work.
                # Start on the final page and delay both endpoints beyond 500ms.
                await legacy_fixture(page, changing_seats=True)
                await scraper._go_to_next_page(page)
                await page.evaluate('''() => {
                    for (const id of ['first','last']) {
                        const node = document.querySelector('#'+id);
                        const original = node.onclick;
                        node.onclick = () => setTimeout(original, 1500);
                    }
                }''')
                for _ in range(2):
                    await scraper._go_to_first_page(page)
                    assert await page.evaluate('current') == 0
                    settled = await scraper._collect_current_view_courses(page, False, 'deferred-endpoints')
                    assert len(settled) == 3
                await legacy_fixture(page)
                first_rows = await scraper._parse_visible_course_tables(page)
                await scraper._go_to_next_page(page)
                second_rows = await scraper._parse_visible_course_tables(page)
                target = next(row['id'] for row in second_rows if row['name'] == '第二页独有')
                await scraper._go_to_first_page(page)
                async def wrong_return(_page, rows, **_kwargs):
                    await _page.evaluate("document.querySelector('#first').click()")
                    return rows
                with patch.object(scraper, '_enrich_with_details', new=wrong_return):
                    try:
                        await scraper._collect_current_view_courses(page, True, 'detail-return',
                            detail_ids={target}, known_ids={row['id'] for row in first_rows + second_rows})
                    except ScrapePageNotReadyError:
                        pass
                    else:
                        raise AssertionError('detail return to another page must not silently skip rows')
            print("PASS scraper browser: identity/value separation, repeated-session final-page updates, delayed movement, broken arrows, detail-return identity, legacy/numeric boundaries")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
