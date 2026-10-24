# 架构、文档与目录维护验收

日期：2026-10-25（北京时间）。本轮维护工具与文档已进入主分支，服务器配置回退副本整理已执行；应用运行代码和进程保持原值。

## 基线与交付

- 起点：本地 `main@e78a30c` clean；生产应用 `04fc2086c487`，服务 `boya-agent`。
- [PR #8](https://github.com/2725238326/boya-agent/pull/8)，合并 `a8bf531`：归档旧状态/待办原文，统一当前架构、目录、开发、部署与运行说明；加入文档本地链接、模块依赖、已跟踪运行文件检查，以及默认只读的目录维护工作流。
- [PR #9](https://github.com/2725238326/boya-agent/pull/9)，合并 `a10debb`：指定配置回退副本的私有归档/恢复；部署备份保留规则排除每周、手工和暂停快照。
- 修复 `deploy/admin_access.md` 的 Windows 绝对链接；部署目录纯 Markdown 排除出应用部署触发和通知配置内容比较。两个批次均未触发应用部署。

## 文档与本机目录

当前事实统一在 [项目状态](PROJECT_STATUS.md) 与 [改进清单](IMPROVEMENT_BACKLOG.md)，整理前原文完整保留在 [历史索引](../archive/README.md)。架构明确学生找课/补位与 Telegram 运维分工，预发布从计划改为已实现的隔离入口。脚本按用途列入 [脚本目录](../../scripts/README.md)，维护按 [目录手册](../operations/DIRECTORY_MAINTENANCE.md) 执行。

本机 7 份历史 PR 临时正文归入 `runtime/reviews/2026-10/`。根目录旧 SQLite 以只读 immutable 方式确认用户表 0、完整性 ok、WAL 0 字节后，将 DB/WAL/SHM 三件套一起保留到 `runtime/legacy/empty-db-20261024/`。清单在忽略的 `runtime/maintenance/local-layout-20261025.json`，恢复须保持原位置空闲并将三件套一起处理。兼容默认数据库路径没有改变，后续直接启动仍可能在根目录创建状态；本地验证使用 staging 启动器。

## 服务器盘点与实际整理

[只读盘点 40109281451](https://github.com/2725238326/boya-agent/actions/runs/40109281451) 成功，未发现七天前的独立诊断捕获，发现 4 份散落配置回退副本；6 个发布恢复目录为空，保留用于追溯。主要逻辑字节占用如下，不能直接等同文件系统已用空间：

| 内容 | 约 MiB |
| --- | --- |
| Playwright 浏览器 | 653.60 |
| 状态备份 | 317.51 |
| 应用日志 | 210.60 |
| 生产 venv | 210.54 |
| 生产数据（含 sidecar） | 14.42 |

[整理 40110481670](https://github.com/2725238326/boya-agent/actions/runs/40110481670) 于 00:12:59 成功：先验证近期 DB 备份完整性，再将 4 份配置回退副本（合计 3603 字节）归入私有批次。

- 归档：`/var/lib/boya-agent/backups/configuration/configuration-20261024T161259Z-92010ff2/`，路径日期使用 UTC。
- 恢复清单：上述目录 `manifest.json`，0600；批次目录 0700。
- 私有盘点：`/var/lib/boya-agent/maintenance/layout-20261025T001258-1792858378.json`。
- 服务前后 MainPID 均为 `3644618`，应用提交仍为 `04fc2086c487b27a5aff71dd1fe05cfe9541248e`，工作树 clean、探活通过。

归档只调整位置，未永久删除备份，不作为释放磁盘的成果。真实配置、数据库、上传、venv 和浏览器路径保留；恢复按同版本工具的 `--restore-manifest`，排他创建原位置，保留归档副本。

## 实际验证

- 本地 Windows Python 3.12 完整发布检查：180 项测试、53 项子测试通过；GNU find 生产匹配检查 1 项跳过，由 Linux CI 执行。
- 最新 [PR CI 40110194840](https://github.com/2725238326/boya-agent/actions/runs/40110194840) 与 [分支 CI 40110192332](https://github.com/2725238326/boya-agent/actions/runs/40110192332)：Python 3.10/3.12 各 181 项测试、53 项子测试通过；隔离 systemd 恢复、四组浏览器脚本、fixture/邮件/outbox、前端检查通过。
- 三个相关工作流 YAML 与内嵌 Bash 语法检查通过。当前文档本地链接、22 个源模块依赖和 Git 路径检查通过；不验证远端 URL、标题锚点或历史归档正文。
- [独立诊断 40110577493](https://github.com/2725238326/boya-agent/actions/runs/40110577493) 于 00:14 成功：服务 active、1 个 driver、DB 完整性 ok；过滤开关 `(10,0,0,0,0,1)`，飞书课程 true/告警 false，Telegram 运维 false；通知队列只有历史 succeeded，无 pending/processing/failed。
- 公网 `/healthz`、首页、`/subscribe`、`/portal`、`/QRcode` 均 200，未认证 `/api/status` 为 401；课程接口 14 条。资源快照：根盘 70%、约 12 GiB 可用，应用内存约 406 MiB，主机 available 731 MiB，Swap 使用约 1.6 GiB。

## 维护中发现的采集波动

日志在目录整理前的 00:07、00:09 已出现“分页重复，快照不完整”，以及空快照被健康检查拒绝、浏览器重建超时与冷却；00:06:49、00:08:50、00:12:04 仍有 7 页/131 条完整采集成功。随后公网 last_success 推进到 00:14:24，但该观察点 degraded=true、refreshing=true。这表明成功采集与失败重试交替，不能报告为长期稳定。

DB 与公开列表未被异常空结果覆盖，14 条有效记录各关键字段无缺失/负数/时间倒置，仍有 1 条历史记录名额待核对。此次归档没有修改采集代码或重启进程；分页重试根因已列入 P1，后续应单独复现、补回归并按应用发布流程修复。该问题不以一次 healthz 或完整 CI 通过替代真实环境验收。

Telegram 运维依用户决定待网络打通后启用；邮件仍暂停，自动选课冻结。真实邮箱送达、运维通道、长期资源与备份恢复继续按当前清单推进。
