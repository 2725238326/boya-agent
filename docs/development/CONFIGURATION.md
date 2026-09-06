# 配置说明

文档用途：作为配置名称、默认值、来源和生效方式的单一说明。
面向读者：开发者和部署维护者。
文档状态：当前配置事实；更新时间：2026-09-04。
来源优先级：启动/运行代码和数据库模型 > `config/.env.example` > `config/default_config.json` > 历史文档。

## 两类配置

部署配置来自环境变量，通常在进程启动时读取，修改后重启 `boya-agent`。业务配置保存在 SQLite 的 `filter_config` 表中，可由管理员 API 修改；修改抓取间隔和每日摘要计划会立即更新相应调度，其他开关在下一次任务读取配置时生效。

`config/default_config.json` 保留作初始业务配置参考，当前启动流程不会自动读取它；空数据库的真实初始值来自 `FilterConfig` 模型和 `init_db()`。不要在多个文档另写一套默认数字。

## 环境变量

| 名称 | 类型 / 默认值 | 敏感 | 生效 | 用途 |
| --- | --- | --- | --- | --- |
| `BUAA_USERNAME` | 字符串 / 空 | 是 | 重启 | 北航课程系统登录账号 |
| `BUAA_PASSWORD` | 字符串 / 空 | 是 | 重启 | 北航课程系统登录密码 |
| `TELEGRAM_BOT_TOKEN` | 字符串 / 空 | 是 | 重启 | Telegram Bot 凭据 |
| `TELEGRAM_CHAT_ID` | 字符串 / 空 | 否（但应保护） | 重启 | Telegram 接收会话 |
| `SMTP_SERVER` / `SMTP_PORT` / `SMTP_USE_TLS` | `smtp.gmail.com` / `587` / `true` | 否 | 重启 | 默认 SMTP 传输 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 空 / 空 | 是 | 重启 | 默认 SMTP 登录 |
| `SMTP_FROM`、`SMTP_FROM_VERIFY`、`SMTP_FROM_LOGIN`、`SMTP_FROM_NOTIFY`、`SMTP_FROM_REMINDER` | 空 | 否 | 重启 | 各类邮件发件人覆盖值 |
| `SMTP_VERIFY_*` | 空（未设置时回退默认 SMTP） | 多数是 | 重启 | 验证/登录邮件专用 SMTP |
| `SMTP_NOTIFY_*` | 空（未设置时回退默认 SMTP） | 多数是 | 重启 | 通知/提醒邮件专用 SMTP |
| `SMTP_PROXY` | 空 | 否 | 重启 | 仅邮件模块使用的代理 |
| `WEB_HOST` / `WEB_PORT` | `127.0.0.1` / `5000` | 否 | 重启 | Flask 监听地址；生产不应直接暴露公网 |
| `WEB_SECRET_KEY` | 无安全默认值；至少 32 字符 | 是 | 重启 | Flask 密钥、验证码摘要密钥；缺失则拒绝启动 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 空 | 是 | 重启 | 应用层 Basic Auth；需同时存在 |
| `ADMIN_API_TOKEN` | 空 | 是 | 重启 | 可选应用层 Bearer token；与 Basic 二选一即可 |
| `APP_PUBLIC_BASE_URL` | `https://buaaboya.top`（代码回退值） | 否 | 重启 | 邮件链接和 RSS 自链接的公网基址；生产必须显式设置 |
| `APP_ALLOWED_ORIGINS` | 空 | 否 | 重启 | 允许的跨源来源；为空时不启用 CORS，仍允许同源 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 否 | 重启 | 业务时间和数据库旧字段的解释时区 |
| `DATABASE_PATH` | `boya_agent.db` | 否 | 重启 | SQLite 文件路径；相对路径相对于工作目录 |
| `AUTH_CHALLENGE_TTL_SECONDS` | `900`，最少 `300` | 否 | 重启 | 邮件一次性链接有效期 |
| `LOGIN_BRIDGE_TTL_SECONDS` | `900`，最少 `60` | 否 | 重启 | 跨设备验证桥接票据有效期 |
| `VERIFICATION_CODE_TTL_MINUTES` | `20`，最少 `5` | 否 | 重启 | 邮箱验证码有效期 |
| `AUTH_CODE_MAX_ATTEMPTS` | `5`，最少 `3` | 否 | 重启 | 单条验证码错误次数上限 |
| `LOGIN_IP_COOLDOWN_SECONDS` | `5`，最少 `2` | 否 | 重启 | 同一来源 IP 的登录邮件冷却 |
| `QRCODE_MAX_FILE_SIZE` | `5242880` 字节，服务端至少 `65536` | 否 | 重启 | 二维码原始文件大小上限 |
| `SCRAPE_INTERVAL_MINUTES` | `10`，运行入口限制为 `1..1440` | 否 | 重启 | 普通完整抓取间隔 |
| `PUSH_URGENT_DIGEST_MINUTES` / `PUSH_SOON_DIGEST_MINUTES` | `5` / `30` | 否 | 重启 | 推送缓冲区刷新窗口 |
| `HOT_COURSE_REMAINING_THRESHOLD` / `HOT_COURSE_FILL_RATIO` | `3` / `0.82` | 否 | 重启 | 热门课程的剩余名额和报名比例阈值 |
| `ACTIVE_ENROLL_SCRAPE_SECONDS` | `30` | 否 | 重启 | 开选窗口巡检间隔 |
| `HOT_COURSE_WATCH_SECONDS` | `15` | 否 | 重启 | 热门课程巡检间隔 |
| `HOT_COURSE_STALE_SECONDS` | `25` | 否 | 重启 | 热点监控数据陈旧阈值 |
| `BROWSER_MAX_SCRAPE_RUNS` | `80` | 否 | 重启 | 浏览器软回收阈值 |
| `BROWSER_HARD_MAX_SCRAPE_RUNS` | 至少软阈值，默认不低于 `140` | 否 | 重启 | 浏览器硬回收阈值 |
| `BROWSER_DEFER_RECYCLE_WHEN_HOT` | `true` | 否 | 重启 | 热点监控期间是否延后回收 |
| `SCRAPE_TASK_TIMEOUT_SECONDS` | `900`，最少 `180` | 否 | 重启 | 单次抓取超时 |
| `COURSE_PAGE_NETWORK_IDLE_TIMEOUT_MS` | `5000`，最少 `1000` | 否 | 重启 | 课程页面动作后的网络空闲等待上限；超时后继续按页面状态判断 |
| `SCRAPE_CAPTURE_DIAGNOSTICS` | `false` | 否 | 重启 | 是否在正常抓取轮次保存课程页诊断截图；失败时仍按错误路径保存必要资料 |
| `AUTO_ENROLL_FAILURE_LIMIT` | `3`，最少 `1` | 否 | 重启 | 自动选课当天累计失败达到阈值后暂停后续尝试 |
| `NOTIFICATION_DRAIN_SECONDS` | `30`，限制为 `10..300` | 否 | 重启 | 持久化通知任务恢复/投递扫描间隔 |

`VERIFICATION_CODE_LENGTH` 固定为 6，不是环境变量。所有时间字符串和数据库中的旧 naive 时间按 `APP_TIMEZONE` 解释；RSS/Atom 对外发布时间转成 UTC。

## 数据库业务配置

管理员 `PUT /api/config` 可修改以下字段。空数据库默认来自 `FilterConfig`：

| 字段 | 默认值 | 实际作用 |
| --- | --- | --- |
| `categories` | `[]` | 全局课程类别筛选；空表示不按类别限制 |
| `self_sign_only` | `true` | 全局筛选是否只保留自主签到课程 |
| `strict_boya_only` | `false` | 是否启用严格博雅规则 |
| `min_remaining` | `1` | 全局筛选的最少剩余名额 |
| `campus_filter` | `""` | 全局校区筛选 |
| `keyword_whitelist` / `keyword_blacklist` | `[]` | 全局关键词白名单/黑名单 |
| `auto_enroll_enabled` | `false` | 自动选课总开关；实验能力，默认关闭 |
| `priority_keywords` | `[]` | 自动选课优先关键词 |
| `confirm_before_enroll` | `true` | 自动选课前是否发送 Telegram 确认提醒；当前是提醒，不会等待用户点击后再执行 |
| `max_auto_enroll_per_day` | `2` | 自动选课每日上限 |
| `email_enabled` | `false` | 自动课程邮件、摘要、提醒和选课结果邮件通道 |
| `telegram_enabled` | `false` | 自动课程 Telegram、摘要、提醒和告警通道 |
| `rss_enabled` | `true` | 是否允许公开读取 `/rss` 和 `/atom` |
| `daily_summary_enabled` | `false` | 是否安排每日摘要 |
| `daily_summary_time` | `21:00` | 按 `APP_TIMEZONE` 执行的摘要时间 |
| `interval_minutes` | `10` | 数据库运行配置；更新时限制为 `1..1440` 分钟并立即调整普通抓取计划 |

RSS 开关控制公开读取，不代表把 RSS 当作邮件或 Telegram 推送渠道。手动邮件推送是管理员明确触发的独立动作；它不应被用来判断自动 `email_enabled` 是否开启。

## 变更安全

修改敏感环境变量后重启并检查 systemd；修改数据库配置前记录旧值。生产变更前备份 SQLite 和二维码目录，使用管理员认证调用配置 API，随后检查 `/healthz`、认证后的 `/api/status` 和日志。不要把真实配置复制进 README、测试、截图或提交历史。
