# BUAA 博雅课程自动推送 Agent — 项目上下文

> 本文件供 AI 编程助手（Codex / Claude Code / Cursor 等）快速理解项目。

---

## 项目简介

一个自动抓取北航博雅选课系统课程、筛选并通过 Telegram / 邮件推送通知的 Agent。
部署在腾讯云 Ubuntu 服务器上，通过 Playwright 操控 headless 浏览器访问 WebVPN 课程系统。

---

## 目录结构

```
BUAA_boya/
├── src/
│   ├── auth.py          # WebVPN + SSO 登录逻辑
│   ├── scraper.py       # 课程列表爬取 + 详情页抓取
│   ├── filters.py       # 过滤引擎（签到方式、名额、关键词）
│   ├── scheduler.py     # APScheduler 定时任务调度
│   ├── models.py        # SQLAlchemy 数据模型
│   ├── enroll.py        # 自动选课逻辑
│   └── push/
│       ├── telegram_bot.py  # Telegram 推送
│       ├── email_push.py    # 邮件推送
│       └── rss_feed.py      # RSS Feed 生成
├── web/
│   ├── app.py           # Flask Web 控制台 API
│   ├── templates/
│   │   └── index.html   # 控制台页面
│   └── static/
│       └── app.js       # 前端交互逻辑
├── config/
│   └── default_config.json  # 默认过滤/推送配置（仅首次 init_db 时使用）
├── deploy/
│   └── boya-agent.service   # systemd 服务文件
├── .env                 # 凭据（不提交 git）
├── requirements.txt
└── src/main.py          # 入口：启动 Flask + APScheduler
```

---

## 核心数据流

```
定时器（APScheduler） → ensure_logged_in() → scrape_courses()
    → _enrich_with_details()  ← 点击"详细介绍"获取签到方式
    → save_courses_to_db()    ← 返回新课程 ID 列表（非 ORM 对象）
    → filter_courses()        ← 基于 FilterConfig 数据库配置
    → send_batch_notifications() → Telegram Bot API（需代理）
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
  - `sign_method`：列表页的"选课方式"（直接选课/预选等）
  - `check_in_method`：详情页的"签到方式"（**自主签到/常规签到**）⬅️ 过滤用这个
  - `description`：课程介绍（详情页）
- `FilterConfig` 表：用户配置（过滤规则、推送开关等）
- **重要**：`save_courses_to_db()` 返回 `List[str]`（ID 列表），不是 ORM 对象，避免 Session 脱离问题

### 过滤引擎（`src/filters.py`）
`self_sign_only` 检查 `check_in_method`（签到方式），不检查 `sign_method`（选课方式）

### 推送（`src/push/telegram_bot.py`）
- 需要通过代理（服务器在国内，Telegram 被墙）
- 代理配置：`.env` 中 `HTTPS_PROXY=http://127.0.0.1:7890`
- 消息中显示 `check_in_method`，不显示 `sign_method`

### Web 控制台（`web/app.py` + `web/static/app.js`）
- Flask 服务，端口 5000
- 「仅自主签课」过滤：查询 `check_in_method.contains("自主")`
- 课程卡片标签：优先显示 `check_in_method`，无则 fallback 到 `sign_method`

---

## 服务器信息

- IP：`49.233.248.86`
- 项目路径：`/home/boya-agent/`
- Python venv：`/home/boya-agent/venv/`
- systemd 服务：`boya-agent`（主服务）、`mihomo`（代理）
- 代理：mihomo（Clash Meta），监听 `127.0.0.1:7890`，控制 API `localhost:9090`
- 当前节点：`🇯🇵 日本高速04|BGP|流媒体`

### 常用命令

```bash
# 服务管理
sudo systemctl restart boya-agent
sudo journalctl -u boya-agent -f

# 修改数据库配置
cd /home/boya-agent && /home/boya-agent/venv/bin/python -c "
from src.models import *; init_db(); s=get_session()
c = s.query(FilterConfig).first()
c.self_sign_only = True
c.min_remaining = 1
c.telegram_enabled = True
s.commit(); s.close()
"

# 清空课程重新抓取
/home/boya-agent/venv/bin/python -c "
from src.models import *; init_db(); s=get_session()
s.query(Course).delete(); s.commit(); s.close()
"
sudo systemctl restart boya-agent

# 切换代理节点
curl -X PUT http://127.0.0.1:9090/proxies/%F0%9F%9A%80%20%E8%8A%82%E7%82%B9%E9%80%89%E6%8B%A9 \
  -H "Content-Type: application/json" \
  -d '{"name": "🇯🇵 日本高速04|BGP|流媒体"}'
```

---

## .env 配置项

```env
BUAA_USERNAME=学号
BUAA_PASSWORD=密码
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=7338513888
HTTPS_PROXY=http://127.0.0.1:7890
HTTP_PROXY=http://127.0.0.1:7890
SCRAPE_INTERVAL_MINUTES=720
```

---

## 已知问题 / 待优化

- [ ] **分页抓取**：目前只抓第一页，`_go_to_next_page()` 已实现但可能需要调试
- [ ] **会话超时**：博雅系统会话约30分钟失效，`_check_and_recover_session()` 已实现但未充分测试
- [ ] **推送汇总模式**：目前有新课程即立刻推，可增加"每日汇总"推送选项
- [ ] **筛选规则 UI**：Web 控制台已有基础配置，可进一步完善（校区筛选、时间段筛选等）
- [ ] **RSS 推送**：已实现 `/rss` 端点，未充分测试
- [ ] **自动选课**：`src/enroll.py` 已有框架，未经实战测试
- [ ] **Telegram 消息格式**：`选课截止: 未知` 问题，`enroll_end` 未能正确解析
