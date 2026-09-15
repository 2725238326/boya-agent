"""Bounded, unauthenticated SSH banner check before production transfers."""

import os
import socket
import sys
import time


def main() -> int:
    host = os.environ.get("SERVER_HOST", "").strip()
    if not host:
        print("SERVER_HOST is required", file=sys.stderr)
        return 1
    deadline = time.monotonic() + 15
    try:
        with socket.create_connection((host, 22), timeout=5) as connection:
            print("TCP connection established", flush=True)
            buffer = b""
            while len(buffer) < 8192:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("SSH banner deadline exceeded")
                connection.settimeout(remaining)
                data = connection.recv(1024)
                if not data:
                    raise ConnectionError("server closed connection before SSH banner")
                buffer += data
                if any(line.startswith(b"SSH-") for line in buffer.splitlines()):
                    print("SSH banner received; authentication has not been tested")
                    return 0
            raise ConnectionError("SSH banner missing within 8192 bytes")
    except (OSError, TimeoutError) as exc:
        print(f"SSH preflight failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
