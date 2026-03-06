# BUAA 博雅课程自动推送 Agent — 项目上下文

> 本文件供 AI 编程助手（Codex / Claude Code / Cursor 等）快速理解项目。

---

## 🚀 项目简介

一个自动抓取北航博雅选课系统课程、智能分级过滤并通过 **邮件** 推送通知的 Agent。
部署在腾讯云 Ubuntu 服务器上，通过 Playwright 持久化浏览器会话（Persistent Browser Session）高频无头刷新 WebVPN 课程系统。
对外提供基于 **Apple-style 毛玻璃 UI** 的订阅与选课管理门户（无密码 Token 登录），对内提供管理员控制台与 Telegram 异常告警机制。

---

## 📂 核心目录结构

```text
BUAA_boya/
├── src/
│   ├── auth.py          # WebVPN + SSO 登录与 Session 恢复逻辑
│   ├── scraper.py       # 课程列表与详情爬取、🔥 **退课捡漏检测**
│   ├── filters.py       # 过滤引擎（过滤无聊课程、指定校区、必须自主签到等）
│   ├── scheduler.py     # APScheduler 调度（3分钟高频刷新、复用浏览器、过期清理）
│   ├── models.py        # SQLAlchemy O-R 映射 (Course, EmailSubscriber, PushLog 等)
│   ├── enroll.py        # 自动选课逻辑（可挂接）
│   └── push/
│       ├── telegram_bot.py  # 现仅用于 Admin 系统告警（连续报错通知、状态查询）
│       └── email_push.py    # 邮件推送引擎（分发定制流、退订、选课提醒）
├── web/
│   ├── app.py           # Flask Web 后端 (API 端点、Token 鉴权、页面路由)
│   ├── templates/
│   │   ├── index.html       # 管理员监控仪表盘 (受 Nginx auth_basic 保护)
│   │   ├── portal.html      # 用户主门户面板（查看课程、设置提醒、修改订阅偏好）
│   │   └── subscribe.html   # 新用户订阅入口页
│   └── static/
│       ├── portal.css   # Apple 质感 UI 核心样式 (阴影、圆角、毛玻璃)
│       ├── portal.js    # 用户门户前端逻辑 (自动分类满课、高亮退课)
│       └── app.js       # Admin 控制台逻辑
├── .env                 # 服务端环境凭据（包含 Gmail SMTP / Telegram 密钥等，未提交 git）
├── requirements.txt
└── src/main.py          # 服务入口：启动 Flask + APScheduler (多线程并发隔离)
```

---

## 🔄 核心数据流

```text
定时器（3分钟/次） → _ensure_browser() 复用/重建实例 → scrape_courses()
    → 对比前后名额检测 "退课捡漏"
    → save_courses_to_db() (入库去重)
    → filter_courses() 
    → 分级送入推送队列 _classify_push_urgency():
        🔴 即将开始 (<1h / 正选课) → 【直接发邮件】
        🟡 紧急 (1h~12h)         → 压入 `urgent` 缓冲 (每 3h 批推)
        🟢 稍后 (12h~24h)        → 压入 `soon` 缓冲 (每 12h 批推)
        🔵 较远 (>24h)           → 压入 `daily` 缓冲 (每日天明汇总推)
    → _sync_course_lifecycle() (删除已结束 30min 以上的课程)
```

---

## 🛠 关键技术细节

### 1. 爬虫与会话保持（`scraper.py`, `auth.py`）
- 目标 URL：WebVPN 代理后的博雅系统。
- **持久化策略**：为了将间隔压缩到 `3分钟` 以实现近实时监控，系统**不再每次启动关闭浏览器**。维护一个全局持久的 Playwright Context，保持 Cookie 不掉线。
- **退课捡漏机制**：拦截更新时比对 `old_remaining == 0` 和 `new_remaining > 0`的课程，将其提权到极高优先级，标记为 🔥 立即推送，并前端高亮提醒。

### 2. 轻量化用户身份认证（Email-Token 机制）
- 不设置密码。用户在 `/subscribe` 输入邮箱后，会收到包含持久 Token 的独特链接（类似于 Slack 的 Magic Link）。
- 后续访问门户全程携带 LocalStorage 内的 Token。
- `<br>` 用户可以自助退订、调整目标校区、仅看自主签到等。

### 3. Apple-style 用户体验（`portal.html`）
- **UI 设计**：极致模仿苹果主页质感。使用 `.5px` 边框、弥散阴影 `shadow-card`、药丸按钮 `border-radius: 980px` 和毛玻璃 `backdrop-filter` 向用户提供高级感。
- **折叠展示**：不再让"已满课程"污染视图，剥离到折叠栏。

### 4. Nginx 代理与安全
- Flask 运行于 `127.0.0.1:5000`。
- Nginx 接管 `80/443`。对前台用户开放 `/portal`, `/subscribe`, `/api/*`。
- 对管理后台 `/` 单独设置 `auth_basic` 拦截防御扫描。

### 5. Telegram 的身份重定位
- **废弃**用户端 TG 群发功能（防止机器人风控封禁、且多用户群发有瓶颈）。
- **转型**为仅限本人的“服务器探针 / Admin Bot”。当连续数次 WebVPN 崩溃或抓取异常时，Bot 警报推送至开发者手机。

---

## 🖥 服务器信息部署

- IP：`202.112.129.236` / `49.233.248.86`
- 域名：`buaayqq.eu.cc`, `www.buaayqq.eu.cc`
- 项目路径：`/home/boya-agent/` (主分支)
- 服务：`boya-agent`（主服务）、`mihomo`（代理）
- HTTP 代理：`127.0.0.1:7890`

```bash
# 查看日志
sudo journalctl -u boya-agent -f

# 强制重启与拉新
cd /home/boya-agent && git pull && sudo systemctl restart boya-agent
```
