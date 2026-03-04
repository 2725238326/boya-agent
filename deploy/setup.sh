#!/bin/bash
# BUAA 博雅课程推送智能体 - 一键部署脚本
# 用法: bash deploy/setup.sh

set -e

APP_DIR="/home/ubuntu/boya-agent"
SERVICE_NAME="boya-agent"

echo "================================================"
echo "  BUAA 博雅课程推送智能体 - 部署脚本"
echo "================================================"

# 1. 系统依赖
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv

# 2. 创建项目目录
echo "[2/6] 设置项目目录..."
mkdir -p "$APP_DIR"
cp -r . "$APP_DIR/"
cd "$APP_DIR"

# 3. Python 虚拟环境
echo "[3/6] 创建虚拟环境并安装依赖..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. 安装 Playwright 浏览器
echo "[4/6] 安装 Playwright Chromium..."
playwright install chromium
playwright install-deps chromium

# 5. 配置环境变量
echo "[5/6] 配置环境变量..."
if [ ! -f .env ]; then
    cp config/.env.example .env
    echo "⚠️  请编辑 $APP_DIR/.env 填入你的凭据！"
    echo "    nano $APP_DIR/.env"
fi

# 创建日志目录
mkdir -p logs

# 6. 部署 systemd 服务
echo "[6/6] 部署 systemd 服务..."
sudo cp deploy/boya-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable $SERVICE_NAME

echo ""
echo "================================================"
echo "  部署完成！"
echo "================================================"
echo ""
echo "后续步骤:"
echo "  1. 编辑配置: nano $APP_DIR/.env"
echo "  2. 启动服务: sudo systemctl start $SERVICE_NAME"
echo "  3. 查看日志: sudo journalctl -u $SERVICE_NAME -f"
echo "  4. 访问控制台: http://<服务器IP>:5000"
echo "  5. RSS 订阅: http://<服务器IP>:5000/rss"
echo ""
echo "常用命令:"
echo "  sudo systemctl status $SERVICE_NAME    # 查看状态"
echo "  sudo systemctl restart $SERVICE_NAME   # 重启"
echo "  sudo systemctl stop $SERVICE_NAME      # 停止"
echo ""
