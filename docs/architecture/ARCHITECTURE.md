# 系统架构

文档用途：解释当前组件、数据流、数据库和外部依赖。
面向读者：开发者、测试人员和运维人员。
文档状态：当前实现说明；更新时间：2026-09-04。
相关代码：`src/`、`web/`、`deploy/`。

## 总体拓扑

```text
浏览器 / 邮件链接
        │ HTTPS
        ▼
Nginx（TLS、请求大小、Basic Auth、反向代理）
        │ 127.0.0.1:5000
        ▼
Flask web.app ───── SQLite boya_agent.db
        │                    │
        │                    └─ 课程、订阅、提醒、通知、二维码、日志
        ▼
asyncio + APScheduler
        │
        ├─ Playwright → 北航 WebVPN / 博雅课程系统
        ├─ SMTP → 邮箱验证、课程推送和提醒
        └─ Telegram → 可选课程通知、提醒和故障告警
```

`src/main.py` 是唯一进程入口。它加载环境文件、检查安全配置、初始化数据库，在线程中运行 Flask，在 asyncio 事件循环中运行调度器，并先执行一次完整抓取。

## 模块职责

| 模块 | 职责 | 不应承担的职责 |
| --- | --- | --- |
| `src/main.py` | 启动、环境检查、日志初始化 | 业务路由和数据库查询 |
| `src/auth.py` | WebVPN/SSO 登录和页面登录状态 | 邮箱用户认证 |
| `src/scraper.py` | DOM/网络解析、字段归一化、去重、健康检查、入库 | 推送策略 |
| `src/course_state.py` | 签到标签、报名窗口、过期、热门和剩余名额 | HTTP 响应和数据库提交 |
| `src/filters.py` | 全局筛选和自动选课候选 | 邮件 HTML |
| `src/scheduler.py` | 周期任务、浏览器生命周期、缓冲区、推送触发、课程生命周期 | 页面渲染 |
| `src/models.py` | SQLAlchemy 模型、SQLite 初始化和增量迁移 | 请求权限判断 |
| `src/notification_jobs.py` | 持久化通知任务、幂等键、租约、退避和结果收口 | 渠道模板和课程状态判断 |
| `src/push/email_push.py` | SMTP、邮件模板、用户筛选、通知事件和邮件去重 | Flask 路由 |
| `src/push/telegram_bot.py` | Telegram 通知、提醒和告警 | 用户门户会话 |
| `src/push/rss_feed.py` | RSS/Atom XML | 数据库查询 |
| `src/qrcode_service.py` | 二维码文件验证、存储、重复和公开数据整形 | 管理员认证 |
| `web/security.py` | 管理认证、同源检查、响应头、邮箱脱敏 | 业务数据读写 |
| `web/app.py` | 路由、输入验证、会话 Cookie、服务编排 | 复杂文件校验和推送模板 |
| `web/qrcode_feature.py` | 二维码页面和路由适配 | 具体图片存储规则 |

## 抓取流

1. 调度器根据普通间隔、活跃报名窗口和热门课程监控触发 `full` 或 `quick` 任务。
2. Playwright 复用当前进程浏览器；登录失效时执行 WebVPN/SSO 登录，失败会重建浏览器并重试。
3. 抓取器遍历课程视图，优先解析可见 DOM，必要时使用 Locator 回退和网络响应补充。
4. 课程按名称、时间、教师、地点、校区等字段生成稳定 ID，并处理视图合并和近重复。
5. 健康检查发现快照过稀或异常时拒绝覆盖数据库。
6. 正常数据写入 SQLite；已结束或报名已截止的记录进入生命周期同步，长期过期记录由清理任务处理。
7. 新课、退课补位和已开选监控分别进入即时推送或摘要缓冲区；课程邮件/Telegram 投递再进入 SQLite outbox，由独立扫描任务领取。

## 课程状态单一来源

`src/course_state.py` 提供以下公共语义：

- `get_check_in_display_label`：文本含“自主”或“自选”时显示“自主签到”，否则显示“常规签到”。
- `is_enrollment_open`：报名尚未开始、已截止、课程结束或数据库标记过期时均为 false。
- `is_course_expired`：数据库 `expired`、课程结束时间、报名截止时间任一满足即视为不再可报名/公开展示。
- `is_hot_course`：先要求报名窗口有效，再按剩余名额阈值或填充率判断。

网页、筛选器、邮件、调度器、RSS 和二维码模块都应复用这些函数，不自行复制边界条件。

## 时间处理

`src/time_utils.py` 读取 `APP_TIMEZONE`，默认 `Asia/Shanghai`。数据库字段继续保存业务时区的 naive datetime 以兼容旧 SQLite 数据；外部 RSS/Atom 时间转换为带时区的 UTC。新增代码不得直接调用 `datetime.now()`。

## 用户认证请求流

```text
提交邮箱
  ├─ 新用户/未验证 → verify code + 一次性 verify challenge
  └─ 已验证用户   → login code + 一次性 login challenge
                    │
                    ├─ 邮件链接：GET 确认页，用户点击后 POST 消费 challenge
                    └─ 页面验证码：校验摘要、过期和尝试次数
                              │
                              ▼
                    签发 HttpOnly、SameSite=Lax Cookie
                              │
                              ▼
                    后续门户 API 只信任 Cookie 对应的 verified/active 用户
```

邮箱验证和登录页面的 GET 只展示确认页，不消费链接；用户点击确认按钮后才 POST 消费，以减少邮箱安全扫描器误用一次性链接的影响。数据库只保存 challenge 摘要，验证码只保存应用密钥 HMAC 摘要。

## 推送流

- 邮件：`email_enabled` 为 true 时，按订阅者校区、类别、自主签到、暂停状态和去重事件发送。
- Telegram：`telegram_enabled` 为 true 时，发送全局课程通知、每日摘要、临近选课提醒和连续失败告警。
- 每日摘要：由 `daily_summary_enabled` 控制，且每个通道仍受对应开关控制。
- RSS/Atom：由 `rss_enabled` 控制公开源，不依赖邮件或 Telegram 开关。
- 所有用户通知时间和去重记录写入 `notification_events`；传统课程推送记录写入 `push_logs`。
- 课程邮件和课程 Telegram 推送按订阅者/课程生成幂等任务。任务经过 `pending → processing → succeeded/failed` 状态流转，处理中的任务带租约，临时失败按指数退避，服务重启后由 `NOTIFICATION_DRAIN_SECONDS` 定时任务继续处理。
- 当前 outbox 只覆盖课程推送；选课提醒、每日汇总和站点调整通知仍保留旧的直投路径，不能在汇报中写成“所有通知已统一”。

## 二维码流

上传请求必须带 verified/active 门户 Cookie；贡献者邮箱从会话解析，忽略表单中的身份字段。服务层读取文件上限 +1 字节、验证 Pillow 图片内容、尺寸、真实格式和 SHA-256，生成随机相对路径，记录 pending。公开列表、公开文件路由和排行榜只处理 approved + active 且课程未过期的数据。

## 数据库边界

SQLite 使用 WAL、`busy_timeout` 和有限重试，单进程部署时由各请求/任务创建短生命周期 Session。`init_db()` 先 `create_all()`，再为旧库增加缺失字段和索引；本次新增 `email_auth_challenges`、`notification_jobs` 表、邮箱代码字段和二维码内容哈希字段，以及对应索引，不删除旧字段。outbox 对外部渠道保持至少一次语义，不能宣称绝对 exactly-once。
