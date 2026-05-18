#!/bin/bash
# WC Analytics 部署脚本
# 在 GCP 服务器上执行此脚本完成部署

set -e

PROJECT_DIR="/home/ubuntu/football"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="$BACKEND_DIR/venv"
SERVICE_NAME="wc-analytics"

echo "========================================"
echo "WC Analytics 部署脚本"
echo "========================================"

# ────────────────────────────
# 1. 系统依赖
# ────────────────────────────
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip nginx git

# ────────────────────────────
# 2. 代码部署
# ────────────────────────────
echo "[2/6] 部署代码..."
if [ ! -d "$PROJECT_DIR" ]; then
    # 首次部署：从git克隆或本地复制
    echo "请先将代码上传到 $PROJECT_DIR"
    exit 1
fi

cd "$BACKEND_DIR"

# ────────────────────────────
# 3. Python 虚拟环境
# ────────────────────────────
echo "[3/6] 配置 Python 环境..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ────────────────────────────
# 4. 环境变量
# ────────────────────────────
echo "[4/6] 配置环境变量..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  请编辑 $BACKEND_DIR/.env 填入真实配置"
fi

# ────────────────────────────
# 5. 初始化数据库
# ────────────────────────────
echo "[5/6] 初始化数据库..."
python3 -c "from models import init_db; init_db(); print('Database initialized')"

# ────────────────────────────
# 6. 系统服务 + Nginx
# ────────────────────────────
echo "[6/6] 配置系统服务..."

# systemd service
sudo tee /etc/systemd/system/$SERVICE_NAME.service > /dev/null <<EOF
[Unit]
Description=WC Analytics Backend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$BACKEND_DIR
Environment=PYTHONPATH=$BACKEND_DIR
EnvironmentFile=$BACKEND_DIR/.env
ExecStart=$VENV_DIR/bin/uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Generate self-signed cert for initial setup
echo "[6a] Generating self-signed SSL cert..."
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/ssl/private/nginx-selfsigned.key \
    -out /etc/ssl/certs/nginx-selfsigned.pem \
    -subj "/C=CN/ST=Beijing/L=Beijing/O=WC-Analytics/CN=localhost" 2>/dev/null || true

# Nginx 配置
sudo tee /etc/nginx/sites-available/wc-analytics > /dev/null <<EOF
# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name _;
    return 301 https://\$host\$request_uri;
}

# HTTPS
server {
    listen 443 ssl http2;
    server_name _;

    ssl_certificate /etc/ssl/certs/nginx-selfsigned.pem;
    ssl_certificate_key /etc/ssl/private/nginx-selfsigned.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options "nosniff" always;

    location / {
        root $PROJECT_DIR/static;
        index index.html;
        try_files \$uri \$uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_cache_bypass \$http_upgrade;
    }

    location /static/ {
        alias $PROJECT_DIR/static/;
        expires 1d;
    }
}
EOF
server {
    listen 80;
    server_name _;  # 接受所有域名，或填入你的域名

    # 静态文件（前端页面）
    location / {
        root $PROJECT_DIR/static;
        index index.html;
        try_files \$uri \$uri/ /index.html;
    }

    # API 代理到 FastAPI
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_cache_bypass \$http_upgrade;
    }

    # 静态资源（如果有单独的前端build）
    location /static/ {
        alias $PROJECT_DIR/static/;
        expires 1d;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/wc-analytics /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t

# 启动服务
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME
sudo systemctl restart $SERVICE_NAME
sudo systemctl restart nginx

echo ""
echo "========================================"
echo "部署完成！"
echo "========================================"
echo ""
echo "服务状态检查:"
sudo systemctl status $SERVICE_NAME --no-pager
sudo systemctl status nginx --no-pager
echo ""
echo "健康检查:"
curl -s http://localhost/api/health | python3 -m json.tool 2>/dev/null || echo "服务尚未启动，请等待10秒后重试"
echo ""
echo "下一步:"
echo "  1. 编辑 $BACKEND_DIR/.env 填入真实配置"
echo "  2. 如需 HTTPS: sudo certbot --nginx -d your-domain.com"
echo "  3. 访问 http://your-server-ip/ 查看页面"
