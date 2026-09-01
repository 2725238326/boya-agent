# API 清单

文档用途：按 Flask 当前注册路由记录权限、数据和状态变化，作为新增接口的安全检查表。
面向读者：前后端开发者、运维和安全审阅者。
文档状态：当前代码清单；更新时间：2026-09-02。
核对依据：`web/app.py`、`web/qrcode_feature.py`、`web/security.py` 和 `deploy/nginx_boya.conf`。

## 约定

- “公开”表示不需要门户 Cookie；“会话”表示只接受 `HttpOnly` 的 `portal_token`，邮箱、URL token 和 JSON 字段都不能代替会话。
- “管理员”表示应用层 Basic/Bearer 认证；生产还应经过 Nginx `.htpasswd`，两层不能互相替代。
- 所有 POST/PUT/PATCH/DELETE 默认检查 `Origin` 或 `Referer`。没有这两个头的受控脚本请求保持兼容；带头请求必须是当前 Host 或 `APP_ALLOWED_ORIGINS` 中的来源。
- 邮件验证码、一次性链接和邮件操作 token 都是 bearer secret。接口不应把它们写进普通响应、日志或公开页面。
- 旧的 `/api/subscriber/<token>/*` 门户设置接口保留路径兼容，但返回 410；邮件退订/暂停/提醒操作 token 是尚未完全替换的独立风险边界，见 [SECURITY.md](../security/SECURITY.md)。

## 页面和公开读取

| 方法 | 路径 | 用途 | 访问 / 状态 | 敏感性、CSRF 和限流 |
| --- | --- | --- | --- | --- |
| GET | `/` | 公开首页 | 公开；只读 | 公开课程入口；无 Cookie 写入 |
| GET | `/healthz` | 部署和反向代理健康探活 | 公开；只读 | 只返回 `ok`/`unavailable`，不需要管理员认证，不返回业务统计；响应禁止缓存 |
| GET | `/subscribe` | 订阅/登录页面 | 公开；只读 | 无敏感响应 |
| GET | `/verify/<token>` | 展示待确认的邮箱验证页或登录页 | 公开；不消费链接 | URL 含一次性 bearer；页面不会在 GET 消费，需随后 POST；应限制日志和分析采集 |
| GET | `/portal` | 用户门户页面壳 | 公开页面；数据仍需会话 | 不接受 `email` 或 `token` 登录；仅 `portal_token` 决定身份 |
| GET | `/admin`, `/admin/` | 管理后台页面 | 管理员 | 应用层 + Nginx Basic；不应公开缓存 |
| GET | `/console` | 旧后台入口跳转到 `/admin` | 管理员边界 | 应用层保护跳转入口 |
| GET | `/QRcode` | 二维码共享页 | 公开 | 不包含贡献者私有字段 |
| GET | `/QRcode/course/<course_id>` | 课程二维码页 | 公开；无效课程 404 | 课程 ID 可公开；过期课程不能上传或展示 |
| GET | `/qrcode/uploads/<path>` | 读取已审核二维码文件 | 公开；只读 | 只允许安全相对路径、approved + active、未过期课程；否则 404 |

## 公开课程、订阅和认证接口

| 方法 | 路径 | 用途 | 访问 / 是否改状态 | 敏感性、CSRF 和限流 |
| --- | --- | --- | --- | --- |
| GET | `/api/courses` | 课程列表；支持类别、校区、关键词、余量、今日新课等筛选 | 公开；只读 | 课程数据公开；`include_expired` 可显式请求旧课程；无 Cookie CSRF；前端应处理失败响应 |
| GET | `/api/public/insights` | 订阅页可选课程数、热门课程和开抢倒计时 | 公开；只读 | 课程聚合数据；无个人数据 |
| GET | `/api/categories` | 已知课程类别 | 公开；只读 | 无敏感数据 |
| GET | `/rss` | RSS 2.0 | 公开；只读；`rss_enabled=false` 时 404 | 课程数据和外部课程链接；无 Cookie CSRF |
| GET | `/atom` | Atom | 公开；只读；`rss_enabled=false` 时 404 | 同 RSS；发布时间输出 UTC |
| POST | `/api/subscribe` | 创建或重新激活订阅，并发送验证/登录邮件 | 公开；写入订阅者、挑战、验证码 | 会返回用户提交的邮箱用于当前页面，不代表已验证；邮件发送接口按邮箱/IP 冷却；不能据此判断已注册状态 |
| POST | `/api/login/request` | 为已验证且 active 的用户发送一次性登录链接和验证码 | 公开；不直接创建会话 | 对未知邮箱返回相同通用结果；按邮箱/IP 冷却；不返回 bridge ticket 或凭据；验证码只存摘要 |
| GET | `/api/login/<token>` | 跳转到一次性登录确认页 | 公开 bearer；不消费链接 | 只展示 `/verify/<token>?purpose=login`，不会在 GET 建立 Cookie |
| POST | `/api/login/<token>/confirm` | 确认一次性登录链接并建立门户 Cookie | 公开 bearer；写入挑战消费时间和 Cookie | token 摘要查询、短期、一次性；同源检查；条件更新防重复消费 |
| GET | `/api/verify/<token>` | 旧验证入口重定向到 `/verify/<token>` | 公开；不消费 | 不能使用旧订阅 token 完成验证 |
| POST | `/api/verify/<token>/confirm` | 确认一次性验证链接、验证邮箱并建立 Cookie | 公开 bearer；写订阅者和挑战 | 同源检查；挑战条件更新防重复消费；短期一次性 |
| POST | `/api/subscribe/verify-code` | 提交 6 位邮箱验证码 | 公开；验证或登录并建立 Cookie | 同源检查；错误次数上限、过期；数据库只存 HMAC 摘要；成功消费用条件更新 |
| GET | `/api/subscribe/bridge/<ticket>/status` | 查看跨设备验证桥接状态 | 公开 bearer；只读 | 只返回脱敏邮箱、状态和剩余时间；ticket 应视作短期 secret |
| POST | `/api/subscribe/bridge/<ticket>/claim` | 在当前设备领取已验证 bridge ticket | 公开 bearer；写 claimed 时间并建立 Cookie | 同源检查；过期、未验证、已领取均拒绝；一次性 |

## 用户会话接口

| 方法 | 路径 | 用途 | 访问 / 是否改状态 | 敏感性、CSRF 和限流 |
| --- | --- | --- | --- | --- |
| POST | `/api/subscriber/lookup` | 返回当前会话订阅者信息 | 会话；只读 | 返回当前用户完整邮箱和偏好，仅由 Cookie 决定身份；同源检查虽非必要但 POST 仍受全局规则 |
| POST | `/api/remind/<course_id>` | 当前用户注册课程提醒 | 会话；写入提醒 | 同源检查；按课程去重；仅接受未过期且有余量课程 |
| POST | `/api/portal/refresh` | 为门户排队 quick 抓取 | 会话；触发后台任务 | 同源检查；任务锁避免重复抓取；建议前端按钮限频 |
| GET | `/api/portal/refresh/status` | 返回门户刷新进度 | 会话；只读 | 只返回 `is_running`、`last_run`、`last_success`；不泄露后台总量 |
| GET | `/api/subscriber/session` | 读取当前用户和首次引导状态 | 会话；可能更新最近访问时间 | 返回用户邮箱和偏好；Cookie 会话；前端应处理 401 |
| POST | `/api/subscriber/session/onboarding-seen` | 标记已看过首次引导 | 会话；写入时间 | 同源检查；无额外敏感响应 |
| POST | `/api/session/clear` | 清除门户 Cookie | 无需已登录会话；清除 Cookie | 同源检查；不改数据库 |
| PUT | `/api/subscriber/session` | 更新当前用户类别、校区、自主签到和 active 偏好 | 会话；写订阅者 | 同源检查；不接受邮箱或 URL token 作为身份 |
| PUT | `/api/subscriber/<token>` | 历史门户设置路径 | 直接返回 410 | 旧 token 不再是门户凭据 |
| GET | `/api/subscriber/session/reminders` | 当前用户提醒列表 | 会话；只读 | 返回课程和提醒信息；不接受 token 别名 |
| GET | `/api/subscriber/<token>/reminders` | 历史提醒路径 | 直接返回 410 | 防止旧长期 token 访问门户数据 |
| GET | `/api/subscriber/session/notifications` | 当前用户通知时间线 | 会话；只读 | 返回该用户事件，不返回其他用户；`hours` 和 `limit` 有上限 |
| GET | `/api/subscriber/<token>/notifications` | 历史通知路径 | 直接返回 410 | 防止旧长期 token 访问门户数据 |
| POST | `/api/unsubscribe` | 当前用户退订并清除 Cookie | 会话；写 active=false | 同源检查；可重复调用；邮箱操作链接仍另有独立入口 |
| POST | `/api/subscriber/session/pause-push` | 暂停 1..168 小时 | 会话；写暂停时间 | 同源检查；时长有界 |
| POST | `/api/subscriber/session/resume-push` | 恢复推送 | 会话；清除暂停时间 | 同源检查 |
| GET | `/api/portal/highlights` | 门户倒计时、今日新课、待提醒数量 | 会话；只读 | 只返回当前用户相关待提醒；课程聚合数据 |

## 邮件操作 token 接口

| 方法 | 路径 | 用途 | 访问 / 是否改状态 | 风险和限制 |
| --- | --- | --- | --- | --- |
| GET | `/api/unsubscribe/<token>` | 邮件中的退订链接 | 长期订阅 token bearer；写 active=false | 为兼容邮件保留；GET 可能被安全扫描器或转发链接触发，后续应改为确认页/短期操作票据 |
| GET | `/api/pause/<token>` | 邮件中的暂停推送链接 | 长期订阅 token bearer；写暂停时间 | 同上；`hours` 限制为 1..168 |
| GET, POST | `/api/remind/<token>/<course_id>` | 邮件中的“提醒我选课”动作 | 长期订阅 token bearer；写提醒 | GET 会改变状态，存在扫描/误点击风险；只允许 active、verified、未过期且有余量课程 |

## 管理接口

| 方法 | 路径 | 用途 | 访问 / 是否改状态 | 敏感性、CSRF 和限流 |
| --- | --- | --- | --- | --- |
| GET | `/api/config` | 读取全局筛选、通道和调度配置 | 管理员；可能初始化配置行 | 包含运行开关；应用层 + Nginx；不得公开 |
| PUT | `/api/config` | 修改筛选、自动选课、推送和抓取配置 | 管理员；写数据库/调度 | 应用层 + Nginx + 同源检查；间隔限制 `1..1440` |
| POST | `/api/enroll/toggle` | 切换自动选课 | 管理员；写配置 | 高风险状态修改；应用层 + Nginx + 同源检查 |
| POST | `/api/trigger` | 管理员触发 full/quick 抓取 | 管理员；排队/执行任务 | 应用层 + Nginx + 同源检查；任务锁和超时 |
| GET | `/api/status` | 完整运行、浏览器、推送、数据库统计 | 管理员；只读 | 运行内部信息；应用层 + Nginx，不公开 |
| GET | `/api/logs/push` | 最近推送日志 | 管理员；只读 | 运维数据；应用层 + Nginx |
| GET | `/api/logs/enroll` | 最近选课日志 | 管理员；只读 | 可能含课程系统错误文本；应用层 + Nginx |
| GET | `/api/subscribers` | 订阅用户、推送和活跃度聚合 | 管理员；只读 | 含完整邮箱和业务数据；应用层 + Nginx，禁止缓存/公开 |
| POST | `/api/test-email` | 用课程数据发测试邮件 | 管理员；外部副作用 | 目标邮箱和 SMTP 副作用；应用层 + Nginx + 同源检查 |
| POST | `/api/manual-push` | 手动向活跃邮件订阅者发送课程 | 管理员；外部副作用/写日志 | 应用层 + Nginx + 同源检查；明确动作，不等同自动 `email_enabled` |
| POST | `/api/cleanup-expired` | 删除超过指定天数的过期课程 | 管理员；删除数据库记录 | 应用层 + Nginx + 同源检查；天数限制 `1..3650`，执行前备份 |
| POST | `/api/admin/broadcast/service-update` | 向已验证用户发送站点通知 | 管理员；外部副作用 | 应用层 + Nginx + 同源检查；邮件数据敏感 |
| POST | `/api/admin/subscriber/<id>/toggle-active` | 激活/停用订阅者 | 管理员；写用户状态 | 应用层 + Nginx + 同源检查；日志脱敏 |
| POST | `/api/admin/subscriber/<id>/clear-pause` | 清除用户暂停 | 管理员；写用户状态 | 应用层 + Nginx + 同源检查 |
| POST | `/api/admin/subscriber/<id>/pause-push` | 管理员暂停用户推送 | 管理员；写用户状态 | 应用层 + Nginx + 同源检查；时长有界 |

## 二维码接口

| 方法 | 路径 | 用途 | 访问 / 是否改状态 | 敏感性、CSRF 和限流 |
| --- | --- | --- | --- | --- |
| GET | `/api/qrcode/context` | 返回课程二维码上下文、公开统计、脱敏贡献榜 | 公开；只读 | 不返回完整邮箱、文件路径或审核字段 |
| GET | `/api/qrcode/uploads` | 查询公开二维码 | 公开；只读 | 仅 approved + active + 未过期课程；贡献者邮箱脱敏 |
| POST | `/api/qrcode/uploads` | 上传二维码 | 已验证会话；写 pending 文件/记录 | 同源检查；登录后自动识别贡献者；扩展名、解码、大小、尺寸、哈希和路径校验 |
| GET | `/api/admin/qrcode/uploads` | 查看审核记录 | 管理员；只读 | 返回私有字段；应用层 + Nginx |
| PATCH, POST | `/api/admin/qrcode/<upload_id>` | 审核、拒绝或下架二维码 | 管理员；写审核状态和 active | 应用层 + Nginx + 同源检查；状态仅 pending/approved/rejected/expired |

## 维护规则

新增路由后必须同时更新本清单、相关权威文档和回归测试。若接口使用 Cookie 改状态，必须保留同源检查并说明没有 `Origin/Referer` 的脚本兼容边界；若接口使用 URL bearer，优先改为短期、一次性和确认页，并记录扫描器/转发风险。
