#!/usr/bin/env bash
# run_server.sh — Start AutoDev v4 server.
# Run from anywhere: bash autodev/run_server.sh
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 AutoDev v4 — http://localhost:8000"
echo "   Package root: $PARENT_DIR"

# Kill any existing process on port 8000
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
sleep 0.5

# Find python3.12 or fall back to python3
PYTHON=$(which python3.12 2>/dev/null || which python3)
echo "   Python: $PYTHON"

cd "$PARENT_DIR"
exec "$PYTHON" -m uvicorn autodev.server:app --host 0.0.0.0 --port 8000
