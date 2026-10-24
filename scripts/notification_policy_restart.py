"""Compare effective process notification policy without printing credentials."""

import argparse
import os
from pathlib import Path


def needs_restart(actual, requested):
    def flag(value):
        return str(value or '').strip().lower() == 'true'
    # Enabling operations remains conservative: credentials/proxy may have
    # changed. Disabled legacy Feishu alerts have no runtime effect in this app.
    if flag(requested.get('OPS_ENABLED')) or flag(actual.get('TELEGRAM_OPS_ENABLED')):
        return True
    if flag(actual.get('FEISHU_COURSES_ENABLED')) != flag(requested.get('COURSES_ENABLED')):
        return True
    return any((actual.get(key) or '').strip() != (requested.get(key) or '').strip()
               for key in ('FEISHU_WEBHOOK_URL', 'FEISHU_WEBHOOK_SECRET'))


def process_environment(pid):
    try:
        chunks = Path(f'/proc/{int(pid)}/environ').read_bytes().split(b'\0')
        return dict(chunk.decode('utf-8').split('=', 1) for chunk in chunks if b'=' in chunk)
    except (OSError, ValueError, UnicodeError):
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    args = parser.parse_args()
    print('true' if needs_restart(process_environment(args.pid), os.environ) else 'false')


if __name__ == '__main__':
    main()
