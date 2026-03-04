# 🎓 BUAA 博雅课程自动推送智能体

定期抓取博雅选课系统的课程信息，按偏好过滤后通过 Telegram / 邮件 / RSS 推送通知，并支持可选的自动选课功能。

## ✨ 功能特性

- 🔐 **SSO 自动登录** - 统一认证自动登录/续期
- 🕷️ **智能抓取** - Playwright 绕过加密 API，直接读取渲染后课程数据
- 🎛️ **灵活过滤** - 按类别、签到方式、校区、名额、关键词多维度过滤
- 📡 **多渠道推送** - Telegram Bot / 邮件 / RSS Feed
- 🎯 **自动选课** - 可选功能，带意愿优先级和确认提醒
- 🖥️ **Web 控制台** - 可视化配置和监控
- ⏰ **定时调度** - 每 N 分钟自动抓取一次

## 🚀 快速部署

### 1. 上传到服务器

```bash
scp -r . ubuntu@your-server:/home/ubuntu/boya-agent/
```

### 2. 一键部署

```bash
ssh ubuntu@your-server
cd /home/ubuntu/boya-agent
bash deploy/setup.sh
```

### 3. 配置凭据

```bash
nano /home/ubuntu/boya-agent/.env
```

填入以下信息：
- `BUAA_USERNAME` - 学号
- `BUAA_PASSWORD` - 统一认证密码
- `TELEGRAM_BOT_TOKEN` - Telegram Bot Token（通过 @BotFather 获取）
- `TELEGRAM_CHAT_ID` - 你的 Chat ID
- `SMTP_*` - 邮件配置（可选）

### 4. 启动

```bash
sudo systemctl start boya-agent
```

### 5. 访问

- Web 控制台: `http://<IP>:5000`
- RSS 订阅: `http://<IP>:5000/rss`

## 📁 项目结构

```
├── config/              # 配置文件
├── src/                 # 核心代码
│   ├── main.py          # 入口
│   ├── auth.py          # SSO 登录
│   ├── scraper.py       # 课程抓取
│   ├── models.py        # 数据模型
│   ├── filters.py       # 过滤引擎
│   ├── scheduler.py     # 定时调度
│   ├── enroll.py        # 自动选课
│   └── push/            # 推送模块
├── web/                 # Web 控制台
│   ├── app.py           # Flask 路由
│   ├── templates/       # HTML
│   └── static/          # CSS/JS
└── deploy/              # 部署脚本
```

## 🔧 本地开发

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium

cp config/.env.example .env
# 编辑 .env 填入凭据

python src/main.py          # 正常启动
python src/main.py --once   # 单次运行（测试）
```

## 📡 Telegram Bot 设置

1. 向 `@BotFather` 发送 `/newbot`
2. 获取 Bot Token 填入 `.env`
3. 向你的 Bot 发一条消息
4. 访问 `https://api.telegram.org/bot<TOKEN>/getUpdates` 获取 Chat ID

## ⚠️ 注意事项

- 自动选课功能默认关闭，请在 Web 控制台手动开启
- SSO 凭据存储在 `.env` 中，请确保服务器安全
- 建议配置防火墙仅允许你的 IP 访问 5000 端口
