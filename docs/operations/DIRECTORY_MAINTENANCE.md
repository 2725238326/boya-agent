# 本地与服务器目录维护

更新时间：2026-10-25。目录用途见 [PROJECT_LAYOUT.md](../project/PROJECT_LAYOUT.md)，工具实现见 [maintain_server_layout.py](../../scripts/maintain_server_layout.py)。

## 日常检查

本地与 CI 使用统一入口：

```powershell
.venv\Scripts\python.exe scripts/check_repository.py
.venv\Scripts\python.exe scripts/verify_release.py --require-clean
```

第一条检查当前 Markdown 的本地文件链接、模块依赖方向和已跟踪运行文件；历史归档正文不作为当前导航检查，归档首页仍检查。它不访问互联网，也不验证远端 URL 或标题锚点。第二条还包括依赖、测试、隔离演练和前端检查。

本机 `runtime/`、`logs/`、`.venv/`、`node_modules/` 和根目录兼容默认数据库都是忽略产物。预发布从启动器运行，所有状态写入 `runtime/staging/`；不要直接运行生产调度器来测试目录。

## 服务器先盘点

GitHub Actions 的 **Server Layout Maintenance** 默认只盘点：

```powershell
gh workflow run server-layout-maintenance.yml --ref main -f archive_captures=false
```

输出应用/状态目录的字节数和文件数、遗留根数据库数量、恢复目录、散落配置回退副本数量，以及可归档诊断文件的元数据。它不读取环境文件或数据库内容，不展示凭据，不修改应用工作树或通知配置。工具从本次已审阅 Git 对象提取执行，应用不必为维护工具重新部署。

主要目录：

| 位置 | 用途 | 维护方式 |
| --- | --- | --- |
| `/home/boya-agent` | 应用代码、生产 venv、现有日志与上传 | 保持工作树 clean；运行路径迁移需另行发布 |
| `/var/lib/boya-agent/data` | 当前 SQLite 与 WAL/SHM | 在线备份；禁止按文件年龄搬动 |
| `/var/lib/boya-agent/backups` | 部署、每周、二维码、配置备份 | 按类别核对；配置副本 0600，不发布内容 |
| `/var/lib/boya-agent/backups/configuration/configuration-*` | 从状态根目录收拢的配置回退副本 | 按批次保留原名、摘要和恢复清单；0700 私有目录 |
| `/var/lib/boya-agent/playwright`、`cache` | Chromium 与缓存 | 保留当前与回退需要的版本；不能仅按目录日期清理 |
| `/var/lib/boya-agent/release-recovery.*` | 发布恢复证据和可能的旧 venv | 先核对版本和恢复价值；本工具只盘点 |
| `/var/lib/boya-agent/maintenance` | 私有盘点清单 | 目录 0700、清单 0600；不上传公开工件 |
| `/var/lib/boya-agent/archive/captures-*` | 按批次归档的旧诊断捕获 | 带原路径元数据和 SHA-256，可恢复；不自动删除 |

盘点用文件逻辑字节数，不能直接等同 `df` 的已用空间；归档只是调整位置，不承诺释放磁盘。

## 按清单归档

先查看盘点清单，再执行：

```powershell
gh workflow run server-layout-maintenance.yml --ref main -f archive_captures=true
```

工作流与部署/通知配置串行。开始前要求服务和本机探活正常、工作树 clean；归档前还验证最新数据库备份的完整性。

只处理应用 `logs/` 的**直接子文件**：安全 ASCII 文件名，扩展名为 `.png`、`.html`、`.zip`，修改时间至少七天前，且是单链接普通文件。不递归搬动目录；不处理日志正文、数据库/WAL/SHM、上传、`.env`、key、venv、浏览器或备份。符号链接和硬链接不进入清单。

一小时内生成的清单记录大小、时间、设备和 inode；移动前重新检查。归档与原目录必须同一文件系统，使用原子移动；先写入带 SHA-256 的恢复清单，以支持中断恢复。完成后要求应用提交与 MainPID 均保持原值，再做探活。没有候选文件时成功返回 0 条，不为“整理”制造无用移动。

## 收拢配置回退副本

盘点确认存在散落副本后，可单独执行：

```powershell
gh workflow run server-layout-maintenance.yml --ref main -f archive_captures=false -f organize_rollbacks=true
```

仅处理状态根目录下符合 `feishu-config-rollback.` 加六位字母数字后缀的普通单链接文件，mtime 与 ctime 均超过十分钟。与部署/通知配置互斥，排除正在生成的新副本；文件进入 `backups/configuration/configuration-批次/`，保留原名与 SHA-256 清单。不处理应用 `.env`、手工命名副本、真实密钥或任何数据库。先验证近期 DB 备份，完成后复核原提交、PID 与健康。恢复同样使用下方 `--restore-manifest`，传入实际配置归档的 manifest；排他创建原位置，初始权限 0600，保留归档副本。

部署工作流的自动保留规则仅匹配 `boya_agent-YYYYMMDD-HHMMSS-pre-7位SHA.db` 并保留最近 30 份；每周、手工、暂停快照与配置备份各自管理。本次目录维护不永久删除备份。

## 恢复与后续清理

根据工作流输出的真实 manifest 路径，在服务器执行同版本维护工具。当前生产工作树没有维护脚本，直接从本次已审阅的 `a10debb` Git 对象读取即可；以下 manifest 路径对应本次配置整理，只在需要恢复时执行：

```bash
set -euo pipefail
git -C /home/boya-agent show a10debb:scripts/maintain_server_layout.py | python3 - \
  --app-root /home/boya-agent --state-root /var/lib/boya-agent \
  --restore-manifest /var/lib/boya-agent/backups/configuration/configuration-20261024T161259Z-92010ff2/manifest.json
```

后续批次替换为该次维护工作流的已审阅 SHA 与真实 manifest，不为恢复随意改动业务代码。恢复校验摘要并排他创建目标，拒绝覆盖已有文件；保留归档副本，重复执行会跳过已恢复文件。中断时保留归档和 manifest，先据此恢复或复核。

永久删除、压缩归档、搬迁正在使用的日志/上传、清理浏览器或旧环境不属于此命令。需要释放磁盘时先依据盘点识别实际大项，再写明保留版本、验证备份和恢复步骤。不要运行整个应用目录的递归删除命令。
