# Football Predictor — 部署指南

## 环境要求

- **Python 3.11** (必须，Python 3.10 的 scipy 有损坏的共享库)
- PostgreSQL 14+
- macOS/Linux

## 快速启动

```bash
cd backend

# 1. 安装依赖
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env  # 或编辑 .env
# 确保 SECRET_KEY 和 ADMIN_API_KEY 已设置

# 3. 启动 (开发模式)
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 4. 启动 (生产模式)
./start.sh
# 或
/Library/Frameworks/Python.framework/Versions/3.11/bin/gunicorn -c gunicorn.conf.py main:app
```

## 生产部署

### 使用 systemd (Linux)

```ini
# /etc/systemd/system/football.service
[Unit]
Description=Football Prediction Engine
After=network.target postgresql.service

[Service]
Type=simple
User=football
WorkingDirectory=/opt/football/backend
Environment=PATH=/Library/Frameworks/Python.framework/Versions/3.11/bin
ExecStart=/Library/Frameworks/Python.framework/Versions/3.11/bin/gunicorn -c gunicorn.conf.py main:app
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 使用 Docker

```bash
docker build -t football-predictor .
docker run -p 8000:8000 --env-file .env football-predictor
```

## 关键路径

| 组件 | 路径 |
|------|------|
| Python | `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3` |
| Gunicorn | `/Library/Frameworks/Python.framework/Versions/3.11/bin/gunicorn` |
| Uvicorn | 同上 (随 gunicorn 使用) |
| 静态文件 | `../static/` |
| 数据库 | PostgreSQL (配置在 .env) |

## 监控

- Health check: `GET /api/health`
- Metrics: `GET /api/metrics` (Prometheus)
- Logs: stdout/stderr (gunicorn 默认)

## 故障排查

### scipy 导入失败

确保使用 Python 3.11。Python 3.10 的 scipy 共享库有损坏的 section offset。

```bash
# 验证
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 -c "from scipy.stats import poisson; print('OK')"
```

### 端口被占用

```bash
lsof -i :8000
kill -9 <PID>
```

### 数据库连接失败

检查 `.env` 中的 DATABASE_URL 是否正确。
