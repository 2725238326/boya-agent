# 历史资料

文档用途：说明旧文档的历史背景和当前替代位置。
面向读者：需要追溯决策或旧部署信息的读者。
文档状态：当前归档索引；更新时间：2026-09-02。

历史讨论和邮件预览已从根目录移入本目录，便于追溯，也避免新成员把旧方案误当成当前配置。归档内容不再作为当前行为依据：

| 历史文件 | 当前替代 |
| --- | --- |
| [`legacy/CONTEXT.md`](legacy/CONTEXT.md) | [项目状态](../project/PROJECT_STATUS.md)、[架构](../architecture/ARCHITECTURE.md) |
| [`legacy/now.md`](legacy/now.md) | [项目状态](../project/PROJECT_STATUS.md) |
| [`legacy/BUSINESS_ROADMAP.md`](legacy/BUSINESS_ROADMAP.md) | [项目状态](../project/PROJECT_STATUS.md)；未来事项仍需逐项确认 |
| [`legacy/change-3-07.md`](legacy/change-3-07.md) | [ADR](../decisions/)、[用户行为](../product/PRODUCT_BEHAVIOR.md) |
| [`legacy/PROJECT_OVERVIEW.md`](legacy/PROJECT_OVERVIEW.md) | 重构前静态审计快照；当前事实以 `docs/` 为准 |
| `deploy/admin_access.md` | [生产部署](../operations/DEPLOYMENT.md)；该文件只保留接入补充说明 |
| [`email-previews/`](email-previews/) | 邮件样式静态预览；真实邮件以 `src/push/email_push.py` 为准 |
| [`../../scripts/preview_email.py`](../../scripts/preview_email.py) | 本地邮件预览工具；运行前先安装开发依赖 |

归档资料可以解释“当时为什么这样写”，不能用于复制当前默认值、权限规则、登录流程或部署命令。发现历史文档仍被链接到新流程时，应优先补充当前替代文档链接，而不是悄悄改写历史结论。
