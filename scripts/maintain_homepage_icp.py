"""Temporarily serve the ICP footer on the legacy homepage at the Nginx edge."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re


FILING_URL = "https://beian.miit.gov.cn/"
FILING_NUMBER = "陇ICP备2026001417号-1"
ANCHOR = '</main>\n\n    <script src="/static/home.js'
FOOTER = (
    '<footer style="text-align:center;padding:16px 16px 24px;font-size:14px">'
    f'<a href="{FILING_URL}" style="color:#475569">{FILING_NUMBER}</a></footer>'
)
MARKER = "# BOYA managed ICP footer"
HOME = re.compile(r"(?m)^(?P<indent>[ \t]*)location\s*=\s*/\s*\{\n(?P<body>.*?)^\1\}", re.DOTALL)


def nginx_literal(value: str) -> str:
    return "'" + value.replace("\n", r"\n") + "'"


def change_config(config: str, *, remove: bool = False) -> str:
    matches = list(HOME.finditer(config))
    if len(matches) != 1:
        raise ValueError(f"expected one exact homepage location; found {len(matches)}")
    home = matches[0]
    body = home["body"]
    proxy = re.search(r"(?m)^([ \t]+)proxy_pass (?:\$boya_upstream|http://127\.0\.0\.1:5000);[ \t]*$", body)
    if not proxy:
        raise ValueError("unexpected homepage upstream")
    replacement = f"</main>\n    {FOOTER}\n\n    <script src=\"/static/home.js"
    indent = proxy[1]
    block = "".join(indent + line + "\n" for line in (
        MARKER,
        'proxy_set_header Accept-Encoding "";',
        f"sub_filter {nginx_literal(ANCHOR)} {nginx_literal(replacement)};",
    ))
    if block in body:
        updated = body.replace(block, "", 1) if remove else body
    elif MARKER in config or re.search(r"(?im)^\s*(?:sub_filter\b|proxy_set_header\s+Accept-Encoding\b)", body):
        raise ValueError("homepage filtering already has an unmanaged configuration")
    else:
        updated = body if remove else body + block
    return config[:home.start("body")] + updated + config[home.end("body"):]


class FilingLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.footer_depth = 0
        self.text = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "footer":
            self.footer_depth += 1
        if tag == "a" and dict(attrs).get("href") == FILING_URL:
            self.text = []

    def handle_data(self, data):
        if self.text is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.text is not None:
            self.links.append((self.footer_depth > 0, "".join(self.text).strip()))
            self.text = None
        if tag == "footer":
            self.footer_depth -= 1


def verify(page: str, *, legacy: bool = False):
    parser = FilingLinks()
    parser.feed(page)
    if legacy:
        if parser.links or page.count(ANCHOR) != 1:
            raise ValueError("origin homepage does not match the legacy template")
    elif parser.links != [(True, FILING_NUMBER)]:
        raise ValueError("expected one link with the site filing number inside the homepage footer")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "remove", "verify", "verify-legacy"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    contents = args.path.read_text(encoding="utf-8")
    if args.action in ("apply", "remove"):
        print(change_config(contents, remove=args.action == "remove"), end="")
    else:
        verify(contents, legacy=args.action == "verify-legacy")


if __name__ == "__main__":
    main()
