# 清晰表达审阅记录

## 受众和阅读任务

本次审阅覆盖 README、当前 docs、用户页面模板、前端提示和邮件正文，读者包括普通订阅用户、管理员、开发者和运维人员。普通用户需要知道如何订阅、登录、提醒和暂停；工程读者需要知道实际接口、配置、权限和未决风险。

## 术语问题表

| 原词或旧写法 | 问题 | 修改后的表达 | 适用位置 | 内部名称是否保留 |
| --- | --- | --- | --- | --- |
| `Email Token` / `无密码 Token 登录` | 把实现名称当成用户动作，也掩盖邮箱所有权验证 | “邮箱一次性链接或 6 位验证码登录” | 页面、产品文档 | 只在 ADR/API 说明保留 |
| `persistent browser session` | 容易让人误以为跨进程登录状态已保证 | “当前进程复用浏览器会话；跨重启不保证” | 项目状态、架构 | `Playwright` 保留在技术说明 |
| `session bridge` / `bridge ticket` | 普通用户不知道要做什么 | “跨设备验证后领取登录状态” | 产品页面、邮件 | 只在 API/ADR 保留 |
| `active_watch`、`digest_urgent`、`digest_soon`、`digest_daily` | 调度内部状态直接暴露给读者 | “开选监控提醒”“近期课程摘要”“每日课程摘要” | 邮件标题、产品文档 | 仅代码和接口事件记录保留 |
| `pending`、`approved`、`rejected`、`expired` | 状态值本身缺少结果解释 | “等待审核”“已通过”“未通过”“已失效” | 二维码页面、管理说明 | API 审核字段保留原值并补中文 |
| `Feed` | 英文名不说明用途 | “RSS/Atom 课程内容流” | README、配置和产品文档 | 标准名 `RSS`、`Atom` 保留 |
| `baseline` / `source of truth` | 工程术语不适合普通读者 | “比较基准” / “以此文档为准” | 文档治理 | ADR 或审计上下文可保留原词 |
| “能力闭环、架构升级、全链路” | 没有说明实际动作和结果 | 直接写“发送邮件”“保存记录”“由 Nginx 转发到 Flask” | 当前 docs | 不保留空泛词 |

## 修改后的可用表达

- 订阅页：用户输入邮箱后，系统发送验证邮件；用户输入邮件里的 6 位验证码或点击按钮后，才会进入课程门户。知道邮箱地址本身不能登录。
- 门户：当前设备会保留登录状态。用户可以查看课程、设置类别/校区/签到偏好、注册选课提醒、暂停或恢复推送，也可以退出当前设备或取消订阅。
- 二维码：上传成功只表示图片已提交；管理员审核通过且课程未过期后，图片才会出现在共享列表。
- 管理员：管理页面和管理接口需要管理员认证；普通用户的门户 Cookie 不能调用这些接口。
- 故障提示：把“处理失败”改为“验证链接已过期，请回到订阅页重新发送邮件”“上传失败，请检查图片格式和 5 MB 大小限制”等包含动作和限制的提示。

## 内外名称对照

| 对外呈现 | 内部名称 | 内部名称出现位置 |
| --- | --- | --- |
| 课程门户登录状态 | `portal_token` | Cookie 名、认证代码、API 文档 |
| 邮箱验证/登录挑战 | `EmailAuthChallenge` | SQLAlchemy 模型、ADR、测试 |
| 等待审核 / 已通过 / 已失效 | `pending` / `approved` / `expired` | 数据库、管理 API、服务层 |
| 邮件内容流 | `/rss`、`/atom`、`rss_enabled` | URL、配置和技术文档 |
| 近期开抢提醒 / 每日摘要 | `active_watch` / `digest_daily` | 调度和通知事件记录 |
| 管理员认证 | `ADMIN_USERNAME`、`ADMIN_PASSWORD`、`ADMIN_API_TOKEN` | `.env`、安全文档、部署命令 |

## 事实与范围检查

- 未改变登录权限：邮箱地址本身不能建立会话，必须使用邮件链接或验证码。
- 未改变管理员范围：应用层认证和 Nginx 认证都保留；完整 `/api/status` 现在明确归入管理员边界。
- 未把 RSS 开关描述成邮件推送开关；它只控制 `/rss` 和 `/atom` 的公开读取。
- 未把二维码上传描述成自动公开；公开仍取决于审核、active 状态和课程有效期。
- 未把跨重启 Playwright 会话描述成已实现；生产行为仍待确认。
- 未更改未知的生产域名、证书位置、SMTP、Telegram 或 WebVPN 配置；样例值均标明需替换。

## 未解决问题

1. 生产证书、Nginx 实际配置、SMTP/Telegram 可达性和北航登录行为需要现场验证。
2. 邮件退订、暂停和提醒仍使用长期操作 token；需要产品确认是否接受改为短期确认票据。
3. 是否需要跨进程/多实例限流，以及是否需要跨重启保存 Playwright storage state，当前未作决定。
4. 普通用户是否需要二维码举报入口，当前未提供。

## 实际检查结果

| 检查项 | 方式 | 结果 |
| --- | --- | --- |
| 用户页面和邮件中的内部状态词 | `rg` 扫描模板、静态脚本和 `src/push`，逐项核对提示 | 主要页面改为动作/结果/限制表达；调度状态保留在代码和技术记录 |
| 当前文档与代码/配置的默认值 | 核对 `src/models.py`、`src/main.py`、`web/app.py`、`config/.env.example`、`config/default_config.json` | 已区分环境变量、数据库配置、代码回退值和历史参考 |
| API 权限和敏感字段 | 核对 Flask 路由、`web/security.py`、二维码 DTO 和 Nginx | 已形成 [API_INVENTORY.md](../development/API_INVENTORY.md)，记录公开、会话、管理员和残余 bearer 风险 |
| Python 语法 | `python -m compileall -q src web tests` | 通过 |
| JavaScript 语法 | 对 `web/` 下 6 个 `.js` 文件逐一执行 `node --check` | 通过 |
| TypeScript 7 前端检查 | `npm run check` | 6 个 JavaScript 文件语法检查通过，首个 `checkJs` 样板通过 |
| 自动化测试 | `D:\\Anaconda\\python.exe -m pytest -q tests/test_qrcode_feature.py tests/test_course_state.py tests/test_rss_feed.py tests/test_enroll_safety.py` | `12 passed, 1 skipped`；调度回归另有 `23 passed`，依赖缺失导致的 Web/完整 pytest 收集问题见项目状态 |
| 完整测试收集 | `python -m pytest -q` | 未通过收集：4 个测试模块因当前环境缺少 `sqlalchemy` 或 `playwright` 报 `ModuleNotFoundError`；没有伪造通过结果 |
| 差异格式 | `git diff --check` | 通过；Git 仅提示工作区换行风格，不是空白错误 |
| 真实生产流程 | 当前工作区无生产服务器和外部凭据 | 未运行，列为待确认 |
