"""Build labelled sample cards and an offline structural preview (no secrets)."""

from datetime import timedelta
from html import escape
import json
import re
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.push.feishu_bot import _course_card
from src.time_utils import now as business_now


def sample_cards():
    now = business_now()
    course = SimpleNamespace(id="card-demo", name="课程卡片样例（非真实课程）", capacity=40, remaining=3,
        check_in_method="自主签到", sign_method="直接选课", campus="学院路", category="文化素质",
        location="示例教学楼 201", enroll_start=now - timedelta(hours=1), enroll_end=now + timedelta(hours=2),
        start_time=now + timedelta(days=1), end_time=now + timedelta(days=1, hours=2), last_seen=now)
    opened = SimpleNamespace(**vars(course))
    opened.name = "新课可报名样例（非真实课程）"
    opened.remaining = 18
    preview = SimpleNamespace(**vars(course))
    preview.name = "即将开选样例（非真实课程）"
    preview.enroll_start = now + timedelta(hours=1)
    preview.enroll_end = now + timedelta(days=1)
    cards = [_course_card(course, "snipe", "instant"), _course_card(opened, "new", "instant"), _course_card(preview, "new", "instant")]
    for card in cards:
        card["header"]["title"]["content"] = "【卡片验收演练】" + card["header"]["title"]["content"]
    return cards


def render(card):
    content = []
    for element in card["elements"]:
        tag = element["tag"]
        if tag == "hr":
            content.append("<hr>")
        elif tag == "action":
            for action in element["actions"]:
                style = 'button secondary' if action.get('type') == 'default' else 'button'
                content.append(f'<a class="{style}" href="{escape(action["url"])}">{escape(action["text"]["content"])}</a>')
        elif tag == "note":
            content.append('<p class="note">' + "<br>".join(escape(item["content"]) for item in element["elements"]) + '</p>')
        elif "fields" in element:
            content.append('<div class="fields">' + ''.join('<p>' + escape(field["text"]["content"]) + '</p>'
                for field in element["fields"]) + '</div>')
        else:
            text = escape(element["text"]["content"])
            if element["text"]["tag"] == 'lark_md':
                text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
            content.append('<p>' + text + '</p>')
    return f'<article><header class="{card["header"]["template"]}">{escape(card["header"]["title"]["content"])}</header><div class="body">' + ''.join(content) + '</div></article>'


def main():
    directory = ROOT / 'logs' / 'feishu-cards'
    directory.mkdir(parents=True, exist_ok=True)
    cards = sample_cards()
    (directory / 'samples.json').write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding='utf-8')
    html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>博雅 · 飞书卡片预览</title><style>
    *{box-sizing:border-box}body{margin:0;padding:32px;background:#f4f6f9;color:#1f2329;font:15px/1.6 system-ui,"Microsoft YaHei",sans-serif}
    h1{font-size:26px;margin:0}.intro{color:#646a73;max-width:780px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:24px;align-items:start;margin-top:24px}
    article{background:white;border:1px solid #dee0e3;border-radius:12px;overflow:hidden;min-width:0}header{font-weight:650;padding:14px 20px}
    .orange{background:#fff3e2;color:#9f5000}.red{background:#fde5e5;color:#a92323}.green{background:#e2f5e9;color:#187244}.blue{background:#e5edff;color:#235dc9}
    .body{padding:4px 20px 18px}p{white-space:pre-line;overflow-wrap:anywhere}.fields{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}.fields p{margin:8px 0}
    hr{border:0;border-top:1px solid #eff0f1;margin:16px 0}.button{display:inline-block;background:#3370ff;color:white;padding:7px 17px;border-radius:6px;text-decoration:none;margin:8px 0}
    .button+.button{margin-left:8px}.button.secondary{background:white;color:#245bdb;border:1px solid #b8caff;padding:6px 16px}
    .note{font-size:12px;color:#646a73}@media(max-width:500px){body{padding:16px}}
    </style><h1>博雅 · 飞书消息卡片</h1><p class="intro">本地结构预览，使用与发送相同的卡片 JSON。飞书客户端的字体、间距和最终效果以群内消息为准。以下均为验收演练，非真实课程或故障。</p><main class="grid">'''
    (directory / 'preview.html').write_text(html + ''.join(render(card) for card in cards) + '</main></html>', encoding='utf-8')
    print('Preview saved:', directory / 'preview.html')


if __name__ == '__main__':
    main()
