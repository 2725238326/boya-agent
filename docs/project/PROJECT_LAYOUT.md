# 项目目录与环境边界

更新时间：2026-10-25。本文描述目录职责；当前业务与运行版本见 [项目状态](PROJECT_STATUS.md)，操作步骤见 [目录维护](../operations/DIRECTORY_MAINTENANCE.md)。

## 仓库目录

| 路径 | 内容 | 管理方式 |
| --- | --- | --- |
| `src/` | 领域规则、采集、模型、outbox、调度、渠道 | 按 [架构依赖方向](../architecture/ARCHITECTURE.md) 维护；一次提取一个职责 |
| `web/` | Flask 接口、页面、静态资源与安全适配 | 保持公开 URL/字段兼容；不放数据库和上传原件 |
| `tests/` | 回归、安全、迁移、隔离和维护测试 | 固定样例与 mock，不连接真实校园、SMTP、Telegram、飞书 |
| `config/` | 环境示例与默认参考配置 | 只提交占位配置；现有生产上传目录为忽略产物 |
| `deploy/` | systemd、Nginx、初始化和发布恢复 | 脚本/配置命中生产自动部署路径，Markdown 文档除外；服务样例不会自动替换现场单元 |
| `scripts/` | 检查、预发布、预览和维护入口 | 用途见 [脚本目录](../../scripts/README.md)；保留 CLI 路径兼容 |
| `.github/workflows/` | CI、部署、通知配置、独立诊断和目录维护 | 区分只读检查与生产变更；生产变更共享互斥组 |
| `docs/architecture`、`product`、`development`、`operations`、`security` | 当前实现与操作 | 各主题只保留一份当前主文档 |
| `docs/project/` | 当前状态、清单、发布与验收记录 | 当前状态保持简洁；带日期证据保留观察窗口 |
| `docs/decisions/` | 重要决策及替代关系 | 保留历史决定，明确何时被替代 |
| `docs/archive/` | 旧状态、旧清单、旧讨论和邮件预览 | 非当前指令来源；从归档索引查替代位置 |
| 根目录配置 | README、依赖/锁定、前端检查、许可证和表达标准 | 不放本轮临时报告与真实业务导出 |

## 本机运行产物

以下内容不进入 Git：

| 路径 | 用途 |
| --- | --- |
| `.venv/`、`node_modules/`、`__pycache__/`、`.pytest_cache/` | 依赖与开发缓存 |
| `runtime/staging/` | 隔离预发布数据库、日志、上传和文件邮件 |
| `runtime/` 其他子目录 | 临时审阅正文和受控维护报告，避免散落根目录 |
| `logs/` | 离线浏览器截图、卡片预览及本地诊断 |
| `key/`、真实 `.env` | 本机私有配置；不要输出或同步到仓库 |
| 根目录 `boya_agent.db` | 未指定 DATABASE_PATH 时的兼容默认路径，可能由旧测试/导入产生 |

使用 `scripts/run_staging.py`，由启动器把状态放到 `runtime/staging/`。本次不迁移兼容默认数据库路径，以免改变已有启动行为；未知数据库、备份和上传不得按“看起来旧”直接删除。脚本检查会拒绝把运行目录或真实配置加入 Git。

## 环境隔离

| 环境 | 位置 | 数据边界 |
| --- | --- | --- |
| 本地开发 | `E:\Demo\boya-agent` | `.venv` 和固定样例；生产凭据不复制到本地 |
| 本地预发布 | 当前 checkout 的 `runtime/staging/`，或独立 `E:\Demo\boya-agent-staging` | 独立端口 ≥5001、DB/日志/上传/邮件；fixture + dry_run，不访问真实校园或通知 |
| 生产 | `/home/boya-agent`，服务 `boya-agent` | 真实采集；持久状态主要在 `/var/lib/boya-agent`；按现场开关启用外部通道 |

预发布已实现并有本地/CI 隔离演练；旧 checkout 须先同步实现，不能只拷贝变量。具体启动和安全边界见 [LOCAL_STAGING.md](../development/LOCAL_STAGING.md)。

## 生产目录

```text
/home/boya-agent/                 应用工作树
  .env                           私有运行配置，root:boya-agent 0640
  venv/                          当前生产依赖，发布时保护回退
  logs/                          当前日志和少量诊断捕获
  config/uploads/qrcode/         现有上传路径，需独立备份
/var/lib/boya-agent/              systemd 状态根目录
  data/boya_agent.db             生产数据库，WAL/SHM 与之一起管理
  backups/                       部署/每周数据库、二维码和配置备份
    configuration/               按批次收拢的私有配置回退副本及 manifest
  playwright/                    当前和回退需要的浏览器二进制
  cache/                         受控缓存
  release-recovery.*/            发布恢复证据，可能含旧 venv
  maintenance/                   私有目录盘点清单
  archive/captures-*/             旧诊断捕获归档和恢复 manifest
  ops-watchdog.json              独立运维状态（启用后）
  .deps-sha256                   最近成功依赖安装标记
```

`maintenance/` 和 `archive/` 由实际维护动作按需创建。生产日志和上传保留现有路径；迁移这些活跃路径需要同时调整配置、systemd 写权限、备份和恢复，另作独立发布。目录归档不会启动 Telegram，也不会改变邮件或自动选课策略。

维护顺序为盘点、核对备份、按明确清单归档、复核健康和进程。工具仅支持两类独立操作：七天前的诊断捕获，以及状态根目录下十分钟前的指定配置回退副本；其他目录只记录用途与容量。永久删除和释放空间按实际大项另行评估。
