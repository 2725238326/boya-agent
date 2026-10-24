"""Read a local Feishu credentials file without echoing its contents."""

import argparse
import asyncio
import os
from pathlib import Path
import re
import sys
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_credentials(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    urls = re.findall(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+", text)
    if len(urls) != 1:
        raise ValueError("Expected one Feishu webhook URL")
    secret = None
    for line in text.splitlines():
        value = line.strip().strip('"\'')
        if not value or value.startswith("#") or urls[0] in value:
            continue
        value = re.sub(r"^(?:FEISHU_WEBHOOK_SECRET|签名密钥|密钥)\s*[:：=]\s*", "", value).strip('"\' ')
        if secret is not None or not re.fullmatch(r"[A-Za-z0-9_-]{10,256}", value):
            raise ValueError("Expected one signing secret on its own line")
        secret = value
    if not secret:
        raise ValueError("Signing secret is missing")
    return {"FEISHU_WEBHOOK_URL": urls[0], "FEISHU_WEBHOOK_SECRET": secret}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--send-test", action="store_true", help="Send 3 clearly labelled acceptance messages to this group")
    parser.add_argument("--upload-github-secrets", action="store_true", help="Store these two credentials in the repository's production environment")
    args = parser.parse_args()
    try:
        config = read_credentials(args.config)
        os.environ.update(config)
        from src.push.feishu_bot import _credential_config, _send_sync
        _credential_config()
        print("Feishu webhook and signing secret parsed; values are hidden.")
        if args.upload_github_secrets:
            for name, value in config.items():
                result = subprocess.run(["gh", "secret", "set", name, "--env", "production"],
                                        input=value, text=True, capture_output=True, cwd=ROOT)
                if result.returncode != 0:
                    raise RuntimeError("GitHub secret upload failed")
            print("Both Feishu credentials stored in GitHub production environment; values hidden.")
        if args.send_test:
            for text in (
                "博雅接入验收【故障告警演练，非真实故障】\n课程抓取连续失败：3 次\n状态：timeout\n此消息用于验证告警签名、中文和换行。",
                "博雅接入验收【恢复通知演练】\n课程抓取已恢复成功。此消息是测试数据。",
                "博雅接入验收【课程样例，非真实课程】\n课程：飞书推送配置验收\n校区：学院路｜签到：自主签到\n剩余名额：5/20\n查看课程：https://buaaboya.top/#courses",
            ):
                if not _send_sync(text, "alerts", probe=True):
                    raise RuntimeError("Feishu acceptance message was not accepted")
            print("PASS: Feishu accepted all 3 signed acceptance messages; confirm visible in the group.")
    except Exception as error:
        # No raw exception text: file parsing and HTTP errors may include secrets.
        print("Feishu check failed:", type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
