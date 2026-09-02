#!/bin/bash
# BUAA 博雅课程推送智能体 - 一键部署脚本
# 用法: bash deploy/setup.sh

set -e

APP_DIR="/home/boya-agent"
RUNTIME_DIR="/var/lib/boya-agent"
SERVICE_NAME="boya-agent"

echo "================================================"
echo "  BUAA 博雅课程推送智能体 - 部署脚本"
echo "================================================"

# 1. 系统依赖
echo "[1/6] 安装系统依赖..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-pip python3-venv

# 应用不以 root 运行。项目代码保持只读，运行时数据放入专用目录。
if ! id -u boya-agent >/dev/null 2>&1; then
    sudo useradd --system --user-group --home-dir "$RUNTIME_DIR" --shell /usr/sbin/nologin boya-agent
fi

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
sudo install -d -o boya-agent -g boya-agent -m 750 \
    "$RUNTIME_DIR" "$RUNTIME_DIR/data" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/playwright"
sudo -u boya-agent env \
    HOME="$RUNTIME_DIR" \
    XDG_CACHE_HOME="$RUNTIME_DIR/cache" \
    PLAYWRIGHT_BROWSERS_PATH="$RUNTIME_DIR/playwright" \
    "$APP_DIR/venv/bin/playwright" install chromium
sudo env PLAYWRIGHT_BROWSERS_PATH="$RUNTIME_DIR/playwright" \
    "$APP_DIR/venv/bin/playwright" install-deps chromium

# 5. 配置环境变量
echo "[5/6] 配置环境变量..."
if [ ! -f .env ]; then
    cp config/.env.example .env
    echo "⚠️  请编辑 $APP_DIR/.env 填入你的凭据！"
    echo "    nano $APP_DIR/.env"
fi

# 创建运行时目录，并只将运行时数据交给非 root 服务用户。
mkdir -p logs config/uploads/qrcode

# 将旧数据库迁移到服务专用目录，避免服务用户获得整个仓库的写权限。
database_setting="$(sed -n 's/^DATABASE_PATH=//p' .env | tail -n 1)"
database_setting="${database_setting:-boya_agent.db}"
case "$database_setting" in
    /*) database_file="$database_setting" ;;
    *) database_file="$APP_DIR/$database_setting" ;;
esac
runtime_database="$RUNTIME_DIR/data/boya_agent.db"
if [ -f "$database_file" ] && [ "$database_file" != "$runtime_database" ]; then
    sudo cp -p "$database_file" "$runtime_database"
    for suffix in -wal -shm; do
        if [ -f "$database_file$suffix" ]; then
            sudo cp -p "$database_file$suffix" "$runtime_database$suffix"
        fi
    done
fi

set_database_path() {
    local config_file="$1"
    local temp_file
    temp_file="$(mktemp)"
    awk -v database_path="$runtime_database" '
    BEGIN { updated = 0 }
    $0 ~ /^DATABASE_PATH=/ {
        print "DATABASE_PATH=" database_path
        updated = 1
        next
    }
    { print }
    END {
        if (!updated) print "DATABASE_PATH=" database_path
    }
    ' "$config_file" > "$temp_file"
    sudo install -o root -g boya-agent -m 640 "$temp_file" "$config_file"
    rm -f "$temp_file"
}

set_database_path "$APP_DIR/.env"
if [ -f "$APP_DIR/config/.env" ]; then
    set_database_path "$APP_DIR/config/.env"
fi

sudo chown root:boya-agent "$APP_DIR/.env"
sudo chmod 640 "$APP_DIR/.env"
if [ -f "$APP_DIR/config/.env" ]; then
    sudo chown root:boya-agent "$APP_DIR/config/.env"
    sudo chmod 640 "$APP_DIR/config/.env"
fi
sudo chown -R boya-agent:boya-agent logs config/uploads "$RUNTIME_DIR"
sudo find logs config/uploads -type d -exec chmod 750 {} +
sudo find logs config/uploads -type f -exec chmod 640 {} +
sudo chmod 755 "$APP_DIR"

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
echo "  4. 访问首页: https://<你的域名>/"
echo "  5. 管理后台: https://<你的域名>/admin"
echo "  6. RSS 订阅: https://<你的域名>/rss"
echo ""
echo "常用命令:"
echo "  sudo systemctl status $SERVICE_NAME    # 查看状态"
echo "  sudo systemctl restart $SERVICE_NAME   # 重启"
echo "  sudo systemctl stop $SERVICE_NAME      # 停止"
echo ""
