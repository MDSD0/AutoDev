"""
run_bridge.py — starts the local AutoDev bridge for the hosted web app.

Usage:
    python3 run_bridge.py

This keeps terminal execution, local file access, Ollama, llama.cpp, and any
other localhost-only integrations on the user's own machine while the UI can
be hosted remotely.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    host = os.getenv("AUTODEV_BRIDGE_HOST", "127.0.0.1")
    port = os.getenv("AUTODEV_BRIDGE_PORT", "8765")
    os.environ.setdefault("AUTODEV_ALLOW_SERVER_SECRETS", "true")

    print(f"AutoDev local bridge starting on http://{host}:{port}")
    print("Use the hosted UI, then click 'Connect Local Bridge'.")

    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "asgi:app",
            "--host",
            host,
            "--port",
            str(port),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
