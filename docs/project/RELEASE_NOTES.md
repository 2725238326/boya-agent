# BOYA Agent 发布与部署记录

文档用途：记录实际发布、服务器更新、线上验收、外部副作用和未决风险。
面向读者：项目负责人、开发者和运维人员。
文档状态：当前发布记录；更新时间：2026-09-07。

## 2026-09-04：抓取结果与通知投递加固

### 结论

应用改动已部署到生产服务器并完成线上复核。真实首轮抓取成功得到 3 门课程，其中 2 门通过筛选；课程邮件 outbox 在观察窗口内完成 22 个任务。主 SMTP 出现多次超时，但重试或回退通道完成投递，Telegram 和自动选课没有启用。

### 目标环境

- 主机：`49.233.248.86`
- 应用目录：`/home/boya-agent`
- 域名：`https://buaaboya.top`
- 服务：`boya-agent`
- 分支：`codex/ts7-and-hardening`
- 应用提交：`2eb1e2a`
- 部署时间：2026-09-04 21:32:46 CST 重启生效

### 本次改动

- 抓取器增加结构化结果状态，区分有课、无课、登录失效、上游不可用、解析失败和超时；失败结果不会误覆盖课程快照。
- 课程邮件和课程 Telegram 推送接入 SQLite outbox，支持幂等键、处理租约、指数退避和服务重启后的恢复；提醒、每日汇总和站点通知仍未全部迁移。
- 加入过期 processing 任务和并发幂等创建的回归测试。
- 接入 TypeScript 7 渐进式检查，并同步开发、架构、配置、部署、运行、产品行为和项目工作标准文档。

### Git 与服务器同步

- 本地提交：`2eb1e2a feat: harden scrape and notification delivery`。
- 服务器工作树已从 `ead117d` 快进到 `2eb1e2a`，发布前后均为 clean。
- 服务器 GitHub deploy key 被标记为 read-only，仅用于拉取；本次使用已授权的 HTTPS Git 凭据完成推送。GitHub `codex/ts7-and-hardening` 已包含本次应用与文档提交，本地分支已设置 upstream，核对结果为 ahead/behind `0/0`，服务器分支与其一致。

### 数据库迁移与备份

- 启动时增量创建 `notification_jobs` 表及状态、渠道和可用时间索引；没有删除旧字段或业务数据。
- `PRAGMA integrity_check` 返回 `ok`。
- 部署前生成 SQLite 一致性备份：`/var/lib/boya-agent/backups/boya_agent-20260904-213116-pre-2eb1e2a.db`。

### 实际验证

- 本地：`65 passed, 1 skipped`；Python 编译检查、JavaScript 语法检查、TypeScript 7 类型检查和 `git diff --check` 通过。
- 服务器：生产依赖满足，`python -m compileall -q src web tests` 通过；`boya-agent` 和 Nginx active。
- 页面/API：主页、订阅页、门户、健康端点、课程/类别接口和 RSS 返回 200；未授权 `/api/status` 返回 401；HTTP 正确跳转 HTTPS。
- 真实业务：北航 SSO 登录成功，课程页解析 3 门课程，2 门进入筛选结果；生产课程数量为 3。
- 通知：22 个邮件任务最终为 `succeeded`，无 pending/processing/failed；观察到主 SMTP 超时，之后由重试或回退通道完成。

### 风险与下一步

- 主 SMTP 仍有连接/响应超时，下一轮应记录每个阶段耗时、回退比例和最终收件结果，并评估是否需要调整 SMTP 配置或超时策略。
- outbox 目前只覆盖课程推送；选课提醒、每日汇总和站点通知仍需分批迁移和测试。
- 真实课程只代表本次观察窗口，不能代替长期稳定性、Telegram 可达性或完整用户体验演练。
- 后续生产发布仍应通过合并到 `main/master` 触发 CI 和自动部署；当前功能分支已同步，但不会因为功能分支推送而自动触发生产工作流。

回滚优先使用已审阅的 Git revert 提交；紧急恢复时保留数据库和 outbox 记录，使用已确认的旧提交恢复代码，并按 [DEPLOYMENT.md](../operations/DEPLOYMENT.md) 补齐正式发布记录。

## 2026-09-07：新版本候选生产发布

### 结论

本批改动已通过持续集成并部署到生产服务器。最终工作流 `34050088901` 成功完成，服务器运行 `main` 的提交 `92e53b2`，服务保持 active，数据库完整性检查通过，公开页面和关键接口可访问。

本次发布没有修改邮件、Telegram 或自动选课的业务配置。启动阶段为避免首次抓取产生外部邮件，部署脚本暂时将数据库中的 `email_enabled` 设为 `0`；首次抓取完成并通过检查后恢复为原值 `1`。这次发布没有把外部邮件送达结果写成已验证事实。

### 目标环境

- 主机：`49.233.248.86`
- 应用目录：`/home/boya-agent`
- 域名：`https://buaaboya.top`
- 服务：`boya-agent`
- 分支：`main`
- 应用提交：`92e53b2c7645bc521dab7e2891b41e7d6cc9e86d`
- 部署完成：2026-09-07 01:57:16 CST
- 工作流：[Deploy to Server #34050088901](https://github.com/2725238326/boya-agent/actions/runs/34050088901)

### 发布前备份与自动检查

- 部署前生成 SQLite 一致性备份：`/var/lib/boya-agent/backups/boya_agent-20260907-015623-pre-92e53b2.db`。
- 部署脚本核对服务器工作树、目标分支和目标提交；服务器工作树保持 clean。
- 生产虚拟环境重新安装 `requirements.txt`，并完成 `src`、`web`、`tests` 的 Python 编译检查。
- 服务启动后检查本机 `/healthz`，再从 `APP_PUBLIC_BASE_URL` 检查主页、订阅页、门户、课程 API 和 RSS。
- 首次抓取日志报告课程系统当前无可选课程，应用随后报告已就绪；这与当前课程周期尚未开展相符，不判定为抓取失败。
- 部署脚本完成数据库完整性检查，并确认 `notification_jobs` 表存在；邮件开关恢复为 `1`，Telegram 和自动选课保持 `0`。

### 独立线上验收

- 本机健康端点：`200`。
- `https://buaaboya.top/healthz`：`200`。
- 主页、订阅页、门户、`/api/courses`、`/rss`：均为 `200`。
- `http://buaaboya.top/`：`301` 跳转到 HTTPS。
- 未授权 `/api/status`：`401`。
- 静态资源缓存：`public, max-age=604800, immutable`。
- SQLite `PRAGMA integrity_check`：`ok`。

### 发布流程修正

首次使用 `2a65f35` 发布时，应用已经在服务器正常启动，但工作流的 `pipefail` 与 `grep -q` 组合造成日志检查误报；服务器状态和邮件开关随后已独立核对并恢复。提交 `90f0aa3` 修正日志匹配，提交 `92e53b2` 改用本机健康检查与服务器最终网址进行分层检查，最终发布任务成功完成。

### 后续观察

- 当前课程周期没有真实可选课程，课程发现、筛选和提醒效果需要在课程实际开放后观察。
- 邮件、Telegram 和自动选课仍按现场配置运行；下一阶段邮件专项工作另行安排。
- 需要继续记录抓取耗时、登录稳定性、通知任务状态和长期错误日志，不能用一次部署结果替代运行周期观察。
