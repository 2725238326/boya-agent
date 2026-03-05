# BUAA 博雅课程自动推送 Agent — 项目上下文

> 本文件供 AI 编程助手（Codex / Claude Code / Cursor 等）快速理解项目。

---

## 项目简介

一个自动抓取北航博雅选课系统课程、筛选并通过 Telegram / 邮件推送通知的 Agent。
部署在腾讯云 Ubuntu 服务器上，通过 Playwright 操控 headless 浏览器访问 WebVPN 课程系统。
具有基于 Nginx 反代安全保护的管理面板，以及基于原生级 iOS 毛玻璃风格的独立邮件订阅系统。

---

## 目录结构

```text
BUAA_boya/
├── src/
│   ├── auth.py          # WebVPN + SSO 登录逻辑
│   ├── scraper.py       # 课程列表爬取 + 详情页抓取
│   ├── filters.py       # 过滤引擎（签到方式、名额、关键词等）
│   ├── scheduler.py     # APScheduler 定时任务调度 (含定时抓取和每分钟刷新的选课提醒)
│   ├── models.py        # SQLAlchemy 数据模型 (含 EmailSubscriber 和 CourseReminder)
│   ├── enroll.py        # 自动选课逻辑
│   └── push/
│       ├── telegram_bot.py  # Telegram 推送
│       ├── email_push.py    # 邮件推送流程 (支持多用户+退订)
│       └── rss_feed.py      # RSS / Atom Feed 生成
├── web/
│   ├── app.py           # Flask Web 控制台 API & 订阅相关的 RESTful 端点
│   ├── templates/
│   │   ├── index.html       # 后台管理控制台页面
│   │   └── subscribe.html   # 多用户邮件订阅页 (基于 Apple Liquid Glass UI，含选课提醒注册回显)
│   └── static/
│       └── app.js       # 前端交互逻辑
├── config/
│   └── default_config.json  # 默认过滤/推送配置（仅首次 init_db 时使用）
├── deploy/
│   ├── boya-agent.service   # systemd 服务文件
│   └── setup.sh             # (废弃，已由 GitHub Actions 替代)
├── .github/workflows/
│   └── deploy.yml           # GitHub Actions 自动化 CI/CD 部署脚本
├── .env                 # 服务端环境凭据（包含 Gmail SMTP / Telegram 密钥等，未提交 git）
├── requirements.txt
└── src/main.py          # 入口：启动 Flask + APScheduler (并行多线程)
```

---

## 核心数据流

```text
定时器（APScheduler） → ensure_logged_in() → scrape_courses()
    → _enrich_with_details()  ← 点击"详细介绍"获取签到方式
    → save_courses_to_db()    ← 返回新课程 ID 列表（非 ORM 对象以免脱离 Session）
    → _sync_course_lifecycle()← 自动标记过期课程 (expired)
    → filter_courses()        ← 基于 FilterConfig 全局配置
    → send_batch_notifications() 
        ├──> Telegram Bot API (带代理)
        ├──> RSS / Atom 生成
        └──> email_push.py → _filter_for_subscriber() → 发送私人定制分流邮件
```

---

## 关键技术细节

### 认证流程（`src/auth.py`）
- 目标 URL：`https://d.buaa.edu.cn/https/77726476706e...203b/system/course-select`（WebVPN 代理后的博雅系统）
- 登录表单在 `loginIframe` 内（非主 frame），选择器：`#unPassword`、`#pwPassword`
- 登录成功判断：URL 包含 WebVPN 编码路径 + `/system/`
- 博雅系统是 **AngularJS SPA**，不支持直接 URL 跳转子页面

### 页面导航（`src/scraper.py`）
1. 先导航到 `/system/home`
2. 点击「我的课程」父菜单展开
3. 点击「选择课程」子菜单（LI 元素有 `href="/system/course-select"`）
4. 解析 `<table>` 课程列表
5. **逐个点击「详细介绍」** 获取每门课的签到方式（`check_in_method`）
6. 点「返回」回列表，循环处理

### 数据库（`src/models.py`）
- SQLite，文件：`boya_agent.db`（项目根目录）
- `Course` 表关键字段：
  - `sign_method`：列表页的"选课方式"
  - `check_in_method`：详情页的"签到方式"（**自主签到/常规签到**）⬅️ 过滤和 UI 展示高度依赖此字段
  - `expired`：根据 `enroll_end` 自动标记为已过期
- `FilterConfig` 表：全局用户配置（过滤规则、默认开启开关等）
- `EmailSubscriber` 表：存储外部订阅用户的个人偏好（支持 categories 列表、自选校区、独立 `self_sign_only` 等），带 token 邮件验证闭环。
- `CourseReminder` 表（新增）：跨线程/跨系统的选课提醒记录器，结合 `scheduler.py` 中的 `check_course_reminders()` 每分钟比对并向用户发邮件+TG 推送（默认选课前 5 分钟响铃）。

### 后端隔离修复（`web/app.py` & `src/scheduler.py`）
由于 Playwright 和 Telethon 高度依赖 `asyncio`，Flask 的 request thread 调用 async 方法会报 Event Loop 相关错误。  
解决方案：`app.py` 中的触发器（如手动抓取）会另起独立的 `threading.Thread` 并注入全新的事件循环 `asyncio.new_event_loop()`，安全实现主从线程分离。

### 部署架构与域名
- 服务端由 Nginx 监听 80 端口，代理至 `127.0.0.1:5000` (Flask)
- 面向公众暴露 `/subscribe`, `/api/subscribe/`, `/api/verify`, `/api/unsubscribe`, `/api/remind`
- 其余全部端点（如 `/`）启用了 Nginx 的 `auth_basic` 模式拦截
- 部署模式为 **Git 推送驱动**(GitHub Actions)

---

## 服务器信息（腾讯云）

- IP：`49.233.248.86`
- 域名：`buaayqq.eu.cc`, `www.buaayqq.eu.cc`（解析通过 DNSPod 或 Cloudflare）
- 项目路径：`/home/boya-agent/`
- Python venv：`/home/boya-agent/venv/`
- 服务：`boya-agent`（主服务）、`mihomo`（Telegram 代理）
- HTTP/HTTPS 代理端口：`127.0.0.1:7890`

### 常用命令

```bash
# 查看日志
sudo journalctl -u boya-agent -f

# 强制重启与拉新
cd /home/boya-agent && git pull && sudo systemctl restart boya-agent

# 重置某门课的推送状态（方便热测试推送通道）
/home/boya-agent/venv/bin/python -c "
from src.models import Course, get_session; s=get_session()
c=s.query(Course).first(); c.pushed=False; s.commit()
"
```

---

## .env 基本项

```dotenv
BUAA_USERNAME=学号
BUAA_PASSWORD=密码

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
HTTPS_PROXY=http://127.0.0.1:7890
HTTP_PROXY=http://127.0.0.1:7890

SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=...
SMTP_PASSWORD=...

WEB_SECRET_KEY=...
```

---

## 待优化清单

- [ ] **日志精简**: 定时器每十分钟跑一次输出较多，考虑降低无更新时的日志输出级别。
- [ ] **Telegram Markdown 逃逸**: 目前直接拼接消息由于有下划线或特殊字符，可能触发 MarkdownV2 parser error，已经退化为 HTML 模式，但依然需小心。
- [ ] **多账号抢课**: `enroll.py` 内部可扩展连接多套教务处凭据。
- [ ] **每日推送汇总机制**: 模型层已有设计 `daily_summary_time`，代码层需补充对应的定时分发拦截流程。
