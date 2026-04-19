"""
run.py — Standalone entry point for AutoDev Native Web.
Run from the autodev/ directory:
    python3 run.py    # starts FastAPI serving the frontend on port 8000
"""
import sys
import os
import subprocess
import threading
import time

# Ensure the parent of autodev/ is on the path so relative imports work
HERE = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)
if PARENT not in sys.path:
    sys.path.insert(0, PARENT)

PKG = "autodev"


def run_server():
    print("Starting AutoDev Native UI & API at http://localhost:8000")
    subprocess.run(
        [sys.executable, "-m", "uvicorn", f"{PKG}.server:app",
         "--host", "0.0.0.0", "--port", "8000"],
        cwd=PARENT,
    )

if __name__ == "__main__":
    run_server()
