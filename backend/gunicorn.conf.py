"""
Gunicorn 生产环境配置

使用: /Library/Frameworks/Python.framework/Versions/3.11/bin/gunicorn -c gunicorn.conf.py main:app
"""

# ─── 进程数 ───
# CPU 核心数 * 2 + 1，但不超过 8
import os
workers = min(int(os.cpu_count() or 4) * 2 + 1, 8)
worker_class = "uvicorn.workers.UvicornWorker"
threads = 4

# ─── 监听 ───
bind = "0.0.0.0:8000"

# ─── 超时 ───
timeout = 120
graceful_timeout = 30
keepalive = 5

# ─── 日志 ───
accesslog = "-"          # stdout
errorlog = "-"           # stderr
loglevel = "info"

# ─── 预加载 ───
preload_app = True

# ─── 进程名 ───
proc_name = "football-predictor"

# ─── 安全 ───
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190
