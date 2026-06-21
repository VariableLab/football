#!/usr/bin/env bash
# 启动脚本 — 使用 Python 3.11 + gunicorn
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.11/bin/python3}"
GUNICORN="$($PYTHON -m gunicorn --version 2>/dev/null || echo "$PYTHON -m gunicorn")"

echo "=== Football Predictor — Production Server ==="
echo "Python:    $($PYTHON --version 2>&1)"
echo "Working:   $SCRIPT_DIR"
echo "Environment: ${APP_ENV:-production}"

# 加载 .env
if [ -f .env ]; then
    set -a; source .env; set +a
    echo "Loaded .env"
fi

# 强制要求密钥
if [ -z "${SECRET_KEY:-}" ]; then
    echo "ERROR: SECRET_KEY is not set in .env"
    exit 1
fi

if [ -z "${ADMIN_API_KEY:-}" ]; then
    echo "ERROR: ADMIN_API_KEY is not set in .env"
    exit 1
fi

# 启动
exec $PYTHON -m gunicorn -c gunicorn.conf.py main:app
