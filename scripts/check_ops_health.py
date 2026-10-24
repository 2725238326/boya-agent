"""Independent local watchdog; notify Telegram only on faults/recovery."""

import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_json(url):
    with build_opener(ProxyHandler({})).open(url, timeout=5) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Local response exceeds limit")
        return json.loads(raw)


def source_unhealthy(payload, now):
    source = payload.get("source") or {}
    if source.get("degraded"):
        return True
    active = any(course.get("enrollment_open") for course in payload.get("data", []))
    if not active:
        return False
    try:
        last = datetime.strptime(source.get("last_success") or "", "%Y-%m-%d %H:%M:%S")
        return now - last > timedelta(minutes=15)
    except ValueError:
        return True


def update_state(previous, problems, notify):
    state = dict(previous)
    for component, status in problems.items():
        failures = int(previous.get(component, 0)) + 1 if status else 0
        state[component] = failures
        if failures >= 3:
            notify(component=component, status=status, failures=failures)
        elif not status:
            notify(component=component, recovered=True)
    return state


def save_state(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.ops-health-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


async def run():
    from src.push import ops_bot
    from src.time_utils import now as business_now
    if not ops_bot.enabled():
        print('Operations watchdog disabled')
        return
    path = Path(os.getenv('OPS_WATCHDOG_STATE', '/var/lib/boya-agent/ops-watchdog.json'))
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    problems = {'service': None, 'source': None, 'disk': None}
    try:
        read_json('http://127.0.0.1:5000/healthz')
    except Exception:
        problems['service'] = 'service_unavailable'
    if not problems['service']:
        try:
            if source_unhealthy(read_json('http://127.0.0.1:5000/api/courses'), business_now()):
                problems['source'] = 'source_stale'
        except Exception:
            problems['source'] = 'source_stale'
    used = shutil.disk_usage(path.parent)
    if used.used / used.total >= 0.9:
        problems['disk'] = 'disk_pressure'
    # Keep an existing source incident unresolved while the application itself
    # is down; a missing sample cannot prove that scraping recovered.
    if problems['service']:
        problems.pop('source')
    state = update_state(previous, problems, ops_bot.queue_state)
    save_state(path, state)
    result = await ops_bot.drain_jobs()
    print('Operations watchdog:', json.dumps({'consecutive_failures': state, 'delivery': result}))


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    try:
        asyncio.run(run())
    except Exception as error:
        print('Operations watchdog failed:', type(error).__name__)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
