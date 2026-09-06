# BOYA Agent 文档中心

文档用途：帮助读者在几分钟内找到当前状态、用户行为、技术实现、部署和历史资料。
面向读者：第一次接触项目的开发者、运维人员、管理员和业务读者。
文档状态：当前文档导航；更新时间：2026-09-07。

这组文档以当前代码、当前配置和当前部署样例为依据。若文档与代码冲突，以代码和配置为准，并在下一次改动中修正文档。

## 唯一事实来源

| 主题 | 权威位置 |
| --- | --- |
| 当前项目状态、已实现能力和风险 | [docs/project/PROJECT_STATUS.md](project/PROJECT_STATUS.md) |
| 系统组件和数据流 | [docs/architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) |
| 用户可见行为 | [docs/product/PRODUCT_BEHAVIOR.md](product/PRODUCT_BEHAVIOR.md) |
| 安全边界 | [docs/security/SECURITY.md](security/SECURITY.md) |
| API 清单 | [docs/development/API_INVENTORY.md](development/API_INVENTORY.md) |
| 生产部署 | [docs/operations/DEPLOYMENT.md](operations/DEPLOYMENT.md) |
| 日常运维 | [docs/operations/RUNBOOK.md](operations/RUNBOOK.md) |
| 开发和测试 | [docs/development/DEVELOPMENT.md](development/DEVELOPMENT.md) |
| 配置项和最终生效规则 | [docs/development/CONFIGURATION.md](development/CONFIGURATION.md) |
| 重要设计决定 | [docs/decisions/](decisions/) |
| 历史资料和替代关系 | [docs/archive/README.md](archive/README.md) |
| 用户可见文字审阅记录 | [docs/review/language-review.md](review/language-review.md) |
| 项目改进工作提示词 | [PROJECT_IMPROVEMENT_PROMPT.md](development/PROJECT_IMPROVEMENT_PROMPT.md) |
| 改进清单和验收目标 | [IMPROVEMENT_BACKLOG.md](project/IMPROVEMENT_BACKLOG.md) |
| 工程与发布体系 | [ENGINEERING_SYSTEM.md](project/ENGINEERING_SYSTEM.md) |
| 新版本候选说明 | [RELEASE_CANDIDATE.md](project/RELEASE_CANDIDATE.md) |
| 进度、测试和部署汇报标准 | [REPORTING_STANDARD.md](project/REPORTING_STANDARD.md) |
| 发布与部署记录 | [RELEASE_NOTES.md](project/RELEASE_NOTES.md) |
| 通用清晰表达提示词 | [PLAIN_LANGUAGE_REVIEW_PROMPT.md](../PLAIN_LANGUAGE_REVIEW_PROMPT.md) |

## 快速入口

- 本地安装和启动：先看 [README.md](../README.md) 与 [DEVELOPMENT.md](development/DEVELOPMENT.md)。
- 生产上线：先看 [DEPLOYMENT.md](operations/DEPLOYMENT.md)，再按 [RUNBOOK.md](operations/RUNBOOK.md) 做上线后检查。
- 常规更新：代码先通过本地/CI 验收，再形成 Git 提交；`main/master` 推送会触发生产工作流，功能分支只有在明确核对后才手动部署。
- 修改环境变量：只按 [CONFIGURATION.md](development/CONFIGURATION.md) 操作，不从历史文档复制默认值。
- 修改认证、课程状态、二维码或管理员边界：先阅读对应 ADR，再补回归测试。
- 开始一轮重要改进：先阅读 [项目改进工作提示词](development/PROJECT_IMPROVEMENT_PROMPT.md)、[改进清单](project/IMPROVEMENT_BACKLOG.md) 和 [汇报标准](project/REPORTING_STANDARD.md)。
- 编写用户文案、技术文档或进度报告：按照 [通用清晰表达提示词](../PLAIN_LANGUAGE_REVIEW_PROMPT.md) 审阅。

## 文档状态标记

文档中使用以下词语：

- **已实现**：当前代码有可追踪实现，但不代表生产环境已经验证。
- **默认关闭**：代码支持，业务配置默认不启用。
- **实验能力**：能运行，但风险或外部依赖较高，不作为核心承诺。
- **部分实现**：相关流程已经存在，但仍缺少必要的产品或运维步骤。
- **历史方案**：仅用于理解过去，不代表当前行为。
- **待确认**：需要在真实部署或外部系统中验证。
