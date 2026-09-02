# BOYA Agent 当前项目状态

文档用途：回答“当前项目是什么、哪些能力可用、哪些风险未确认”。
面向读者：项目负责人、开发者、运维人员和评审者。
文档状态：当前状态主文档；更新时间：2026-09-02。

判定范围：仓库代码、配置样例、部署样例、测试文件，以及 2026-09-02 对生产主机的部署和验收结果。
重要限制：本地 `boya_agent.db` 是 0 字节空文件；生产数据库、凭据和上传文件不进入仓库。SMTP/Telegram 外部发送链路和北航 SSO 真实抓取链路仍需单独演练。

## 一句话结论

项目是一个单进程轻量服务：抓取北航博雅课程，写入 SQLite，按统一课程状态和用户偏好推送到邮件/Telegram，并提供公开课程页、订阅页、用户门户、二维码共享页和管理员后台。认证、管理员边界、二维码隐私、时间/课程状态、运行权限和网页首屏性能已完成一轮收口；`buaaboya.top` 的 HTTPS、健康检查、管理边界和非 root 服务已在生产主机实测通过。

## 当前能力矩阵

| 能力 | 状态 | 事实位置 |
| --- | --- | --- |
| 课程抓取、解析、去重、稀疏快照保护 | 已实现 | `src/auth.py`、`src/scraper.py`、`src/scheduler.py` |
| SQLite 持久化和增量字段迁移 | 已实现 | `src/models.py` |
| 课程状态、签到标签、热门判断 | 已实现 | `src/course_state.py` |
| 全局和用户级筛选 | 已实现 | `src/filters.py`、`src/push/email_push.py` |
| 邮箱订阅、一次性验证、门户会话 | 已实现 | `web/app.py`、`src/push/email_push.py` |
| 用户门户、提醒、通知时间线、暂停和退订 | 已实现 | `web/app.py`、`web/static/portal.js` |
| RSS/Atom | 已实现，受 `rss_enabled` 控制 | `src/push/rss_feed.py`、`web/app.py` |
| 二维码上传、审核、过期访问控制、贡献榜 | 基础版已实现 | `src/qrcode_service.py`、`web/qrcode_feature.py` |
| 管理后台和管理 API | 已实现，应用层和 Nginx 双层保护 | `web/security.py`、`deploy/nginx_boya.conf` |
| TypeScript 7 前端检查 | 已接入，渐进式检查，当前不改变运行时加载 | `package.json`、`tsconfig.json`、`scripts/check-js.mjs` |
| 邮件课程推送 | 已实现，默认关闭 | `FilterConfig.email_enabled` |
| Telegram 课程/摘要/提醒/告警 | 已实现，默认关闭 | `src/push/telegram_bot.py`、`src/scheduler.py` |
| 自动选课 | 实验能力，默认关闭 | `src/enroll.py`、`FilterConfig.auto_enroll_enabled` |
| 跨重启 Playwright 持久会话 | 未实现 | 当前仅复用进程内 browser/context/page |

## 本次重构已完成

- 登录和验证改为短期一次性链接或 6 位验证码；验证码和链接摘要不以明文保存，验证码有过期和尝试次数上限。
- 门户身份只从 `HttpOnly` 会话 Cookie 读取；邮箱不再作为门户身份凭据，旧的长期订阅 token 不能直接登录门户。
- 管理页面和管理 API 增加应用层 Basic/Bearer 校验；状态修改请求增加 Origin/Referer 同源检查；CORS 不再默认全开放。
- 增加安全响应头、反向代理协议识别、生产密钥强制检查和上传请求大小边界。
- 二维码公开列表只显示审核通过且仍有效的记录；公开响应不含邮箱、原始文件名、服务器路径或审核字段；上传做扩展名、实际图片格式、内容、尺寸、大小、哈希和重复校验。
- 统一 `src/course_state.py` 的签到标签、报名窗口、过期和热门判断，并让抓取、筛选、门户、通知、RSS 和二维码使用业务时区。
- RSS/Atom 使用 UTC 发布时间，修复 Atom 中未定义签到标签；RSS 开关现在实际控制公开源。
- 邮件、Telegram、每日摘要、选课提醒和自动选课结果按数据库中的通道开关执行。
- systemd 改用专用低权限用户；Nginx 示例增加 HTTP 到 HTTPS 跳转、TLS server 和遗漏管理接口的鉴权。
- 删除旧的无条件登录邮件实现，前端不再把登录 token 放入 `localStorage`。
- 接入 TypeScript 7.0.2；先对桥接登录脚本启用 `checkJs`，使用 `npm run check` 统一执行 JS 语法和类型检查。
- 增加公开 `/healthz` 探活端点，并让 CI、部署脚本和 Nginx 使用它；详细 `/api/status` 继续保持管理员边界。
- 自动选课增加按业务日累计失败次数的持久化熔断；`confirm_before_enroll` 仍只是提醒，不是阻断式人工审批。
- 门户首屏移除重复课程请求，通知和完整提醒改为按页签懒加载；课程筛选加入请求取消和旧结果保护。
- 提醒序列化改为单次 JOIN，课程/提醒/通知/订阅者高频查询增加幂等组合索引；公开课程、类别、洞察和 RSS/Atom 使用短时缓存。
- 静态资源统一使用带文件版本提示的 URL，并在 Flask/Nginx 层启用长期缓存和文本压缩；历史讨论与邮件预览移入 `docs/archive/`，本地预览改为临时目录输出。

## 默认关闭或实验能力

- `email_enabled=false`、`telegram_enabled=false`：课程广播默认不发送。
- `daily_summary_enabled=false`：每日摘要默认不发送。
- `auto_enroll_enabled=false`：自动选课必须由管理员显式开启，仍受关键词、每日上限和确认设置约束。
- `rss_enabled=true`：公开 RSS/Atom 默认开启，但可以在管理配置中关闭。
- Playwright 浏览器会话只在当前进程复用，跨重启是否保留 SSO 不作保证。

## 已知问题和待确认项

1. 默认 Python 3.13 环境缺少 SQLAlchemy 和 Playwright 等项目依赖；`python -m pytest -q` 在本地收集阶段报告模块缺依赖。服务器使用临时开发依赖环境完成了全量套件验证，但本地环境仍应补齐依赖后再纳入日常开发检查。
2. 生产 HTTPS、Nginx 实际加载结果、systemd 用户权限、公开/管理接口边界和缓存策略已实测；SMTP/Telegram 可达性、真实邮件收件和外部 SSO 抓取行为尚未在本轮演练。服务器定时抓取日志仍出现“无法进入选择课程页面”，需要单独针对上游页面/SSO 流程继续排查。
3. 订阅邮箱和通知事件仍属于业务数据，生产数据库、日志、环境文件和上传目录必须按 [SECURITY.md](../security/SECURITY.md) 保护。
4. 邮件中的退订、暂停和选课提醒仍使用独立的长期操作 token；它们不再是门户登录凭据，但泄露后仍可能触发对应操作，后续可替换为独立的短期操作票据。
5. SQLite 适合单实例轻量运行，不支持无协调的多实例并发写入。
6. `confirm_before_enroll` 当前只发送 Telegram 确认提醒，不会等待用户确认；自动选课虽已有当天失败熔断，但仍应视为高风险实验能力。

## Breaking changes

- `POST /api/login/request` 不再仅凭已注册邮箱建立门户会话；它只发送一次性登录链接和验证码。
- 登录邮件链接先显示确认页，用户点击“确认登录并进入门户”后才建立会话；这是为避免邮件安全扫描器提前消费链接。
- `/portal?email=...`、`/portal?token=...` 不再提供身份登录；门户必须先完成邮箱验证或一次性登录。
- `localStorage` 不再保存门户登录 token。浏览器升级后，用户可能需要重新获取一次邮件。
- 二维码 pending/rejected/过期记录不再能从公开列表或文件地址读取。

## 下一阶段

1. 在本地固定开发环境复现服务器的完整 pytest 结果，并补齐真实邮件、验证码、门户和二维码的端到端演练。
2. 为一次性挑战、代码并发消费、缓存策略和推送失败分类补充集成测试。
3. 持续拆分 `web/app.py`、`src/scheduler.py` 和前端大文件；每次拆分都保留现有接口和测试保护。

## 本轮实际验证

- `python -m compileall -q src web tests`：通过。
- `web/static/` 下 6 个 JavaScript 文件逐一执行 `node --check`：通过。
- `D:\\Anaconda\\python.exe -m pytest -q tests/test_qrcode_feature.py tests/test_course_state.py tests/test_rss_feed.py tests/test_enroll_safety.py`：`12 passed, 1 skipped`。
- `D:\\Anaconda\\python.exe -m pytest -q tests/test_scraper_scheduler_regressions.py`：`23 passed`。
- `D:\\Anaconda\\python.exe -m pytest -q tests/test_web_security.py`：未收集，当前 Anaconda 环境缺少 `flask_cors`。
- `npm run check`：6 个 JavaScript 文件语法检查通过，TypeScript 7 类型检查通过。
- `python -m pytest -q`：未通过收集，4 个测试模块因默认环境缺少 `sqlalchemy` 或 `playwright` 报错；未将环境阻塞伪装成测试通过。
- 服务器临时测试环境：`46 passed`；未向生产运行虚拟环境安装 pytest 或开发依赖。
- 线上 `https://buaaboya.top`：主页、门户、订阅页和公开接口返回正常；`/api/courses`、`/api/categories`、`/rss` 缓存策略生效，静态资源 URL 带版本参数并返回 `public, max-age=604800, immutable`；HTTP 正确跳转 HTTPS，未授权 `/api/status` 返回 401。
- 生产数据库：新增课程、提醒、通知和订阅者组合索引已存在；systemd 服务以 `boya-agent` 用户运行，服务和 Nginx 均 active，部署后健康检查返回 `{"status":"ok","success":true}`。
- Git：`codex/ts7-and-hardening` 已部署至服务器，当前提交为 `4ee5f61`；服务器工作树 clean。
- `git diff --check`：通过；仅有 Git 关于 LF/CRLF 的换行提示。
