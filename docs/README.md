# BOYA Agent 文档中心

文档用途：帮助读者在几分钟内找到当前状态、用户行为、技术实现、部署和历史资料。
面向读者：第一次接触项目的开发者、运维人员、管理员和业务读者。
文档状态：当前文档导航；更新时间：2026-10-25。

这组文档以当前代码、当前配置和当前部署样例为依据。若文档与代码冲突，以代码和配置为准，并在下一次改动中修正文档。

## 唯一事实来源

| 主题 | 权威位置 |
| --- | --- |
| 当前项目状态、已实现能力和风险 | [docs/project/PROJECT_STATUS.md](project/PROJECT_STATUS.md) |
| 架构、文档与服务器目录维护验收 | [MAINTENANCE_2026_10_25.md](project/MAINTENANCE_2026_10_25.md) |
| 飞书群推送配置与回退 | [FEISHU.md](operations/FEISHU.md) |
| 找课、退选补位和运维的实际需求 | [NOTIFICATION_REQUIREMENTS.md](product/NOTIFICATION_REQUIREMENTS.md) |
| Telegram 独立运维检测 | [TELEGRAM_OPS.md](operations/TELEGRAM_OPS.md) |
| 采集准确性、课程卡片和通道分工验收 | [SCRAPE_CARDS_RELEASE_2026_10_24.md](project/SCRAPE_CARDS_RELEASE_2026_10_24.md) |
| 系统组件和数据流 | [docs/architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) |
| 下一版采集与补位通知设计（未实现） | [SCRAPE_DESIGN.md](architecture/SCRAPE_DESIGN.md)、[交互导览](architecture/scrape-design.html) |
| 采集改造批次与故障验收（待执行） | [SCRAPE_IMPLEMENTATION_PLAN.md](project/SCRAPE_IMPLEMENTATION_PLAN.md) |
| 用户可见行为 | [docs/product/PRODUCT_BEHAVIOR.md](product/PRODUCT_BEHAVIOR.md) |
| 安全边界 | [docs/security/SECURITY.md](security/SECURITY.md) |
| API 清单 | [docs/development/API_INVENTORY.md](development/API_INVENTORY.md) |
| 生产部署 | [docs/operations/DEPLOYMENT.md](operations/DEPLOYMENT.md) |
| 日常运维 | [docs/operations/RUNBOOK.md](operations/RUNBOOK.md) |
| 本地与服务器目录盘点、归档和恢复 | [DIRECTORY_MAINTENANCE.md](operations/DIRECTORY_MAINTENANCE.md) |
| 维护工具与产物位置 | [scripts/README.md](../scripts/README.md) |
| 开发和测试 | [docs/development/DEVELOPMENT.md](development/DEVELOPMENT.md) |
| 配置项和最终生效规则 | [docs/development/CONFIGURATION.md](development/CONFIGURATION.md) |
| 重要设计决定 | [docs/decisions/](decisions/) |
| 历史资料和替代关系 | [docs/archive/README.md](archive/README.md) |
| 用户可见文字审阅记录 | [docs/review/language-review.md](review/language-review.md) |
| 项目改进工作提示词 | [PROJECT_IMPROVEMENT_PROMPT.md](development/PROJECT_IMPROVEMENT_PROMPT.md) |
| 消息机器人渠道调研（Telegram 更新、飞书接入） | [MESSAGING_BOT_RESEARCH.md](development/MESSAGING_BOT_RESEARCH.md) |
| 改进清单和验收目标 | [IMPROVEMENT_BACKLOG.md](project/IMPROVEMENT_BACKLOG.md) |
| 工程与发布体系 | [ENGINEERING_SYSTEM.md](project/ENGINEERING_SYSTEM.md) |
| 进度、测试和部署汇报标准 | [REPORTING_STANDARD.md](project/REPORTING_STANDARD.md) |
| 发布与部署记录 | [RELEASE_NOTES.md](project/RELEASE_NOTES.md) |
| 本地预发布边界 | [LOCAL_STAGING.md](development/LOCAL_STAGING.md) |
| 项目目录与环境边界 | [PROJECT_LAYOUT.md](project/PROJECT_LAYOUT.md) |
| 通用清晰表达提示词 | [PLAIN_LANGUAGE_REVIEW_PROMPT.md](../PLAIN_LANGUAGE_REVIEW_PROMPT.md) |

## 快速入口

- 本地安装和启动：先看 [README.md](../README.md) 与 [DEVELOPMENT.md](development/DEVELOPMENT.md)。
- 生产上线：先看 [DEPLOYMENT.md](operations/DEPLOYMENT.md)，再按 [RUNBOOK.md](operations/RUNBOOK.md) 做上线后检查。
- 继续推进：从 [当前改进清单](project/IMPROVEMENT_BACKLOG.md) 选一个可验收批次；部署按当前运维文档，邮件与 Telegram 各自完成真实验收后启用。
- 采集时效改造：按 [下一版设计](architecture/SCRAPE_DESIGN.md) 与 [实施计划](project/SCRAPE_IMPLEMENTATION_PLAN.md) 从分页/恢复止损开始；设计目标不能作为当前性能结论。
- 当前生产实例：`https://buaaboya.top`；应用版本、运行事实和剩余风险见 [PROJECT_STATUS.md](project/PROJECT_STATUS.md)。
- 当前服务器提交、服务状态、数据库完整性、资源和飞书发送核对见 [生产运行快照](project/SCRAPE_CARDS_RELEASE_2026_10_24.md)。
- 常规更新：运行代码、生产依赖或配置样例变更先通过本地/CI 验收，再形成 Git 提交；`main/master` 中命中部署路径的提交会触发生产工作流。单纯文档或工作流调整不会自动重启应用，工作流调整需要显式手动运行。功能分支不进入生产。
- `config/**` 当前属于生产工作流的触发路径；即使只改 `.env.example`，合并到 `main/master` 前也必须完成生产影响审阅。配置样例不含真实凭据，但不能把它当作普通文档变更。
- `deploy/**` 的脚本和配置仍触发应用部署；其中纯 Markdown 补充说明已排除，通知配置的运行内容等效比较也排除这类文档。
- 修改环境变量：只按 [CONFIGURATION.md](development/CONFIGURATION.md) 操作，不从历史文档复制默认值。
- 修改认证、课程状态、二维码或管理员边界：先阅读对应 ADR，再补回归测试。
- 开始一轮重要改进：先阅读 [项目改进工作提示词](development/PROJECT_IMPROVEMENT_PROMPT.md)、[改进清单](project/IMPROVEMENT_BACKLOG.md) 和 [汇报标准](project/REPORTING_STANDARD.md)。
- 编写用户文案、技术文档或进度报告：按照 [通用清晰表达提示词](../PLAIN_LANGUAGE_REVIEW_PROMPT.md) 审阅。

## 历史与发布证据

| 记录 | 用途 |
| --- | --- |
| [采集与学生卡片验收](project/SCRAPE_CARDS_RELEASE_2026_10_24.md) | 10 月 24 日应用上线及最终通道分工证据 |
| [飞书首次接入](project/FEISHU_RELEASE_2026_10_24.md) | 当日较早的混合通知历史，后由学生/运维分工替代 |
| [10 月升级执行](project/UPGRADE_EXECUTION_2026_10_22.md)、[发布验收](project/RELEASE_VALIDATION_2026_10_22.md) | 邮件隔离、线程投递与发布恢复批次 |
| [旧上线计划](project/FINAL_RELEASE_PLAN.md)、[旧候选版本](project/RELEASE_CANDIDATE.md) | 9 月发布窗口，不能作为当前部署清单 |
| [归档索引](archive/README.md) | 整理前的状态、清单和旧方案，以及当前替代位置 |

当前文档的本地链接、模块依赖与已跟踪运行文件由 `scripts/check_repository.py` 检查，统一发布验证和 CI 会执行。状态页不再不断追加互相冲突的“当前”段落；每次更新当前结论，证据保留在独立带日期记录。

## 文档状态标记

文档中使用以下词语：

- **已实现**：当前代码有可追踪实现，但不代表生产环境已经验证。
- **默认关闭**：代码支持，业务配置默认不启用。
- **实验能力**：能运行，但风险或外部依赖较高，不作为核心承诺。
- **部分实现**：相关流程已经存在，但仍缺少必要的产品或运维步骤。
- **历史方案**：仅用于理解过去，不代表当前行为。
- **待确认**：需要在真实部署或外部系统中验证。
