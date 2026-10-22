"""Wait for a fresh successful scrape, allowing bounded startup recovery."""

import argparse
from datetime import datetime
import json
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


def source_state(payload, started_at: datetime, timezone: str) -> str:
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return "response_failed"
    source = payload.get("source")
    if not isinstance(source, dict) or source.get("degraded") is not False:
        return "source_degraded_or_unknown"
    if not source.get("last_success"):
        return "no_success_yet"
    try:
        last = datetime.fromisoformat(source["last_success"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=ZoneInfo(timezone))
        if started_at.tzinfo is None:
            raise ValueError("startup time must include timezone")
        return "ready" if last >= started_at else "success_predates_startup"
    except (TypeError, ValueError):
        return "invalid_success_time"


def fetch_source(url: str):
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_for_source(url, started_at, timezone, *, timeout=240, interval=5,
                    fetch=fetch_source, clock=time.monotonic, sleep=time.sleep):
    if timeout <= 0 or interval <= 0:
        raise ValueError("timeout and interval must be positive")
    deadline = clock() + timeout
    previous = None
    while clock() < deadline:
        try:
            state = source_state(fetch(url), started_at, timezone)
        except Exception as error:
            state = "request_failed:" + type(error).__name__
        if state != previous:
            print("Startup source:", state, flush=True)
            previous = state
        if state == "ready":
            return True
        sleep(min(interval, max(0, deadline - clock())))
    print("Startup source did not become ready within the bounded window", flush=True)
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--started-at", required=True)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--timeout", type=float, default=240)
    args = parser.parse_args()
    ZoneInfo(args.timezone)
    started = datetime.fromisoformat(args.started_at)
    if started.tzinfo is None:
        parser.error("--started-at requires a timezone offset")
    if not wait_for_source(args.url, started, args.timezone, timeout=args.timeout):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
