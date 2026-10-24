# 2026-10-24 飞书接入发布与验收

状态：已部署并启用指定测试群的课程、抓取失败告警及恢复通知。生产运行 `7c633589b66bafe559d41b58b538ea3780580470`；17:37 的独立诊断确认工作树 clean、服务 active/enabled。时间均为 Asia/Shanghai，备份文件名保留原值。

用户确认课程沿用后台全局筛选，并提供受控本机凭据文件位置。凭据通过标准输入写入 GitHub production Secrets，再由独立配置工作流装载到生产；真实值未写入 Git 或日志，`key/` 已排除版本控制。

## 已交付

- 签名 Webhook：固定 HTTPS 主机/路径、拒绝重定向、限频、超时/大小边界和安全日志。
- 课程、每日汇总、抓取失败告警及恢复通知复用 outbox；独立 alerts/courses 开关，持久冷却、重试与恢复，课程投递前重查全局筛选和有效期。
- 抓取只入队，不等待外网；个人邮箱、个人提醒和登录票据不会发群。每日汇总保持原开关，不随飞书启用。
- 本机凭据解析、群验收与 Secrets 导入工具；生产配置备份/回退工作流；实际进程验证和只读分类诊断。
- staging 清除并拒绝飞书配置。没有数据库结构和生产依赖变更。

## 验证与发布证据

| 环节 | 实际结果 |
| --- | --- |
| 本地完整检查 | `verify_release.py --require-clean` 通过；145 项测试、44 项子测试，依赖、编译、隔离预发布、JS/TypeScript、Git 格式检查通过 |
| 云端 CI | [分支 40081680381](https://github.com/2725238326/boya-agent/actions/runs/40081680381)、[PR 40081684937](https://github.com/2725238326/boya-agent/actions/runs/40081684937) 通过；Python 3.10/3.12、浏览器场景及真实独立 systemd 恢复通过 |
| 合并与代码部署 | [PR #4](https://github.com/2725238326/boya-agent/pull/4) 合并为 `7c63358`；[部署 40081818396](https://github.com/2725238326/boya-agent/actions/runs/40081818396) 成功，17:26 重启、17:27 启动采集验收通过 |
| 生产启用 | [Configure Feishu 40082231687](https://github.com/2725238326/boya-agent/actions/runs/40082231687) 成功；服务器签名探测通过，17:30 配置，17:35 采集恢复及实际进程验证通过 |
| 独立运行核对 | [最终诊断 40082696617](https://github.com/2725238326/boya-agent/actions/runs/40082696617) 成功；按任务类型/事件分类，并核对课程 PushLog，避免把跳过任务当作发送 |

本机另向指定测试群发送过 3 条明确标注的告警、恢复和课程样例，接口均 code=0。服务器配置探测另发验收消息。这些均不计入下列真实事件。接口接受不替代人工确认群内可见。

只读诊断增加安全聚合计数，提交 `ae18ad5`；相关发布保护测试 6 项通过。该工作流修改和后续文档提交没有触发生产应用重启；仓库 HEAD 与生产运行提交应分别记录。

## 真实生产事件与当前快照

启用后的首次校园登录超时，连续失败达到阈值后产生真实告警；自动重试登录和采集成功后产生恢复通知。没有制造故障或修改课程数据来强行验收。17:37:55 的诊断结果：

| 飞书事件 | 成功任务数 | 发送依据 |
| --- | ---: | --- |
| 课程提醒（new） | 3 | 课程成功 PushLog 与发送日志 |
| 退课补位（snipe） | 1 | 课程成功 PushLog 与发送日志 |
| 真实抓取故障告警 | 1 | 17:32:51 delivered_count=1 |
| 真实采集恢复通知 | 1 | 17:35:25 本轮 delivered_count=4，含恢复及 3 条课程消息 |

飞书共 6 个 succeeded 任务，无 pending/processing/failed；课程成功 PushLog 共 4 条，最近为 17:36:21。后续筛选命中重复课程并未重复建立首次课程任务。历史 email 6079 个 succeeded，未启动邮件发送。

- 实际进程：`configured=true`、`alerts_enabled=true`、`courses_enabled=true`、`staging_blocked=false`。
- 全局业务配置 `(interval_minutes, email, telegram, daily_summary, auto_enroll, rss) = (10, 0, 0, 0, 0, 1)`；邮件总开关未设置，沿用默认关闭。邮件订阅和登录按钮保持停用。
- 公开健康检查、首页、订阅页、门户和二维码页返回 200；未授权 `/api/status` 返回 401。课程接口 14 门，17:35:30 核对 `degraded=false`、`refreshing=false`，后续继续采集。
- SQLite 94 条课程记录，最近 `last_seen=2026-10-24 17:37:31.132534`，`integrity_check=ok`。
- 1 个 Playwright driver；cgroup 内存 396398592 bytes，限制 1363148800 bytes；主机 available 758 MiB，Swap 使用约 1.5 GiB/2 GiB。根盘 70%，剩余约 12 GiB。

## 备份和回退

- 发布前数据库：`/var/lib/boya-agent/backups/boya_agent-20261024-172621-pre-7c63358.db`。
- 发布前二维码：`/var/lib/boya-agent/backups/qrcode-20261024-172621-pre-7c63358.tar.gz`。
- 发布恢复目录：`/var/lib/boya-agent/release-recovery.1C7SDm`；上一应用提交 `73e04b1daa62`。
- 私有配置备份：`/var/lib/boya-agent/backups/feishu-env-pre-20261024-093050-087023.bak`，权限 0600，含凭据，不应上传或公开。

用途关闭、精确提交检查及失败自动恢复见 [飞书配置手册](../operations/FEISHU.md)。后续纯文档提交不改变服务器 HEAD；再运行配置工作流前，应先通过受控发布同步其目标版本，不绕过检查。

## 尚未完成

- 群内人工可见确认及长期运行观察；本次自然故障/恢复已验证，但不能代表所有未来网络故障。
- 持续观察校园登录超时、已有翻页无变化告警、Swap 和磁盘水位；未因本次接入进行生产清理。
- 邮件优化代码已在此前升级中部署，真实 SMTP、测试收件箱/垃圾箱、验证登录、发信域名认证和分阶段开放仍需专项验收；尚无指定测试收件邮箱，本次没有向真实邮箱发送。
- 群内命令、私聊和富卡片未纳入本次交付；自动选课保持冻结，每日摘要保持关闭。外部消息仍有至少一次投递的窄重复窗口。

设计见 [ADR 0006](../decisions/0006-feishu-group-notifications.md)。
