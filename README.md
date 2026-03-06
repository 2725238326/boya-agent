#  BUAA 博雅课程自动推送智能体

基于 Playwright 自动化抓取博雅选课系统的实时课程信息，支持多维度内容筛选。通过 Web 控制台可视化管理，提供 Telegram、RSS 通知，并附带邮件订阅系统。

## ✨ 核心特性

- 🔐 **SSO 自动化** - 自动处理北航统一身份认证登录与 Token 续期
- 🕷️ **防反爬抓取** - 基于 Playwright 绕过前端加密，直接读取渲染后数据
- 🎛️ **智能筛选引擎** - 支持基于课程类别、校区、名额、关键词黑白名单、签到方式（自主/常规）的灵活过滤
- 📧 **多用户邮件订阅系统** 
  - 精美的原生级 Apple Liquid Glass UI 风格订阅页
  - 支持多用户独立订阅
  - 用户可自定义校区、类别、自主签到等维度的个人推送偏好
  - 支持完整的邮箱验证与一键退订闭环
- 📡 **全渠道推送** - Telegram Bot 通知 / 个人化邮件提醒 / 全局 RSS 订阅流
- 🛡️ **生产级安全部署** 
  - Nginx 反向代理 + HTTP Basic Auth 保护管控台
  - 核心配置均使用 `.env` 与加密存储，敏感信息零硬编码
- 🚀 **自动化 CI/CD** - 基于 GitHub Actions 实现本地 `git push` 后服务器自动完成部署发布

## 📂 核心项目结构

```text
├── config/              # 运行产生的配置文件 (DB、.env、全局 config 等)
├── src/                 
│   ├── main.py          # 核心流程入口
│   ├── auth.py          # WebVPN 与 SSO 登录逻辑
│   ├── scraper.py       # Playwright 页面抓取与解析
│   ├── models.py        # SQLAlchemy 数据模型 (Course, FilterConfig, EmailSubscriber 等)
│   ├── scheduler.py     # APScheduler 定时调度引擎
│   ├── enroll.py        # (保留) 自动抢课模块
│   └── push/            
│       ├── email_push.py   # 邮件推送引擎 (SMTP 处理与 HTML 邮件渲染)
│       └── ...
├── web/                 # Web 控制台与订阅前端
│   ├── app.py           # Flask 后端路由与 RESTful API
│   ├── templates/       
│   │   ├── index.html       # 暗黑科技风控制台面板
│   │   └── subscribe.html   # 🍎 iOS 液态毛玻璃风格邮件订阅页
│   └── static/          # CSS / JS 静态资源
├── deploy/              # 部署描述与服务配置
├── .github/workflows/   # GitHub Actions 自动化部署流水线
└── boya_agent.db        # SQLite 数据库文件
```

## 🚀 部署与架构说明

本项目当前已通过 GitHub Actions 实现自动化部署于 Ubuntu 服务器。

### 1. 架构拓扑
- **前端入口 (Port 80)**：Nginx 作为反向代理服务器
- **拦截控制**：
  - `/subscribe` 与 `/api/*` 系列订阅接口：**完全公开访问**
  - `/` (控制台)、`/rss` 等管理接口：**Nginx Basic Auth 密码保护**
- **应用层**：Flask 原生服务器运行于本地 `127.0.0.1:5000` (由 Nginx 代理) 并在后台多线程中执行 `asyncio` 抓取调度。

### 2. 持续集成流程 (CI/CD)
提交代码到 `main` 分支后，GitHub Actions 会动执行：
1. SSH 登录生产服务器
2. 拉取最新代码
3. 检查并安装缺失的 `requirements.txt`
4. 通过 `systemctl` 软重启 `boya-agent.service` 使更新无缝生效

### 2.1 Nginx 路由建议（避免订阅页弹出账号密码）
- 公开路径必须关闭 Basic Auth：`/subscribe`、`/portal`、`/api/*`、`/static/*`
- 管理后台路径才启用 Basic Auth：`/`
- 可直接参考模板：`deploy/nginx_boya.conf`

### 3. 环境与配置要求
生产环境依赖以下配置（位于服务器部署目录下的 `.env` 文件）：
```dotenv
# === BUAA SSO 登录凭据 ===
BUAA_USERNAME=xxx
BUAA_PASSWORD=xxx

# === Telegram Bot ===
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# === Email SMTP (基于 Gmail 最佳实践) ===
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your_gmail@gmail.com
SMTP_PASSWORD=16位应用专用密码

# === Web 控制台加密 ===
WEB_SECRET_KEY=强大的随机字符串
```

## 💻 本地开发指南

1. **环境准备:**
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **配置填充:**
   拷贝 `config/.env.example` 到根目录重命名为 `.env` 并填入必要信息。

3. **服务启动:**
   ```bash
   python src/main.py
   ```
   访问 `http://127.0.0.1:5000` 进入管理控制台，访问 `http://127.0.0.1:5000/subscribe` 体验邮件订阅页。

## ⚠️ 隐私声明与风控说明

- **账号安全**：教务账号及密码仅于服务器本地环境变量存放，不会进行任何云端同步。
- **爬虫风控**：默认使用带缓冲的模拟等待时长，切勿无节制地调低抓取时间间隔，避免教务系统触发风控拦截。
