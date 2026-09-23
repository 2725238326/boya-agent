# 项目目录与环境边界

文档状态：当前目录约定；更新时间：2026-09-24。

本文只描述仓库中应如何组织代码、文档和运行产物，以及本地、预发布和腾讯云生产之间的边界。它不记录任何账号、密码、Cookie、SMTP 密码、Bot token 或生产数据库内容。

## 目录总览

| 路径 | 内容 | 管理规则 |
| --- | --- | --- |
| `src/` | 抓取、课程状态、调度、数据库、通知和业务服务 | 运行代码；行为变更需配套测试或验证说明 |
| `web/` | Flask 路由、模板、静态资源和页面行为 | 用户界面与接口；不要把生产数据写入仓库 |
| `tests/` | Python 回归和安全测试 | 使用固定样例或 mock，不连接真实北航、SMTP、Telegram 或飞书 |
| `config/` | 配置样例和初始参考配置 | 只提交占位值；真实 `.env` 只存在于受控环境；该目录命中生产工作流触发路径 |
| `deploy/` | systemd、Nginx、部署和主机诊断脚本 | 生产变更先审阅脚本，再由受控工作流执行 |
| `scripts/` | 发布校验、静态检查和辅助脚本 | 产出写入临时/运行目录，不写入仓库 |
| `docs/` | 当前事实、设计、运维、计划和历史记录 | 当前文档链接回事实来源；历史文档保留原始日期并标明历史 |
| `.github/` | CI、部署和只读诊断工作流 | 工作流改动不会自动等同于应用已部署 |
| `requirements*.txt`、`constraints.txt` | 运行/开发依赖及锁定版本 | 用 `uv` 创建隔离环境；变更需记录验证结果 |

## 不应提交的运行产物

以下内容属于本机或服务器运行状态，不进入 Git：

- `.env`、`.venv/`、`node_modules/`、`__pycache__/` 和 Playwright 浏览器缓存；
- `*.db`、`*.db-wal`、`*.db-shm`、日志、截图、HTML、trace 和上传文件；
- 生产数据库、备份、二维码原文件、认证凭据和任何包含用户邮箱的导出文件。

仓库根目录的 `boya_agent.db`、`logs/`、`.venv/` 和 `node_modules/` 若因本地验证出现，只按忽略规则视为运行产物，不复制到预发布或服务器，也不在没有清单和备份的情况下删除。

## 三套环境

| 环境 | 位置 | 数据与账号边界 | 当前用途 |
| --- | --- | --- | --- |
| 本地开发 | `E:\Demo\boya-agent` | 不填写生产北航账号；使用本地 `.venv` 和固定样例 | 编码、单元测试、静态检查 |
| 本地预发布 | `E:\Demo\boya-agent-staging` | 独立 `.venv`、端口 `5001`、数据库和日志；不访问真实北航；通知 dry-run | 配置、迁移、页面和 outbox 验收 |
| 腾讯云生产 | `/home/boya-agent` | 服务器保留北航凭据；数据库 `/var/lib/boya-agent/data/boya_agent.db`；外部通道按现场开关 | 运行 `buaaboya.top` |

本地预发布的 fixture/dry-run 入口仍属于待实现能力；实现前不要启动会访问北航系统的本地调度器。生产凭据不从服务器复制到任何本地环境。

## 生产运行目录

当前已核对的生产路径如下：

- 应用工作树：`/home/boya-agent`；服务：`boya-agent`；Nginx 将 HTTPS 请求转发到 `127.0.0.1:5000`。
- 数据库：`/var/lib/boya-agent/data/boya_agent.db`。
- 备份：`/var/lib/boya-agent/backups/`，包括部署前备份和每周 SQLite 在线快照。
- 应用日志和上传目录以生产 `.env`、systemd 单元和部署脚本的实际值为准，不能从本地路径推断。

服务器目录整理必须遵循：先只读列出文件及归属，再确认备份可读和回滚路径，最后才允许删除明确的过期运行产物。2026-09-24 核对未发现需要立即清理或重启的证据，因此本次不修改生产文件。

## 文档阅读顺序

1. 当前事实：[PROJECT_STATUS.md](PROJECT_STATUS.md) 和 [生产运行快照](PRODUCTION_RUNTIME_2026_09_24.md)。
2. 目录和环境：[LOCAL_STAGING.md](../development/LOCAL_STAGING.md) 与本文。
3. 配置和操作：[CONFIGURATION.md](../development/CONFIGURATION.md)、[DEPLOYMENT.md](../operations/DEPLOYMENT.md)、[RUNBOOK.md](../operations/RUNBOOK.md)。
4. 历史发布或候选记录：只在需要追溯时阅读，并以文档中的日期和“历史”标记为准。
