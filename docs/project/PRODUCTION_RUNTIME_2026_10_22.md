# 2026-10-22 生产运行核对

## 最新：15:57 发布后核对

[发布后诊断 39744108169](https://github.com/2725238326/boya-agent/actions/runs/39744108169) 确认生产运行 `73e04b1daa62`、clean、active/enabled，1 个 Playwright driver，SQLite 完整性 ok，100 条课程、最近 last_seen=15:56:46。通知任务仍为 6079 条历史 succeeded/email，无待处理任务；外部通道关闭，RSS 开启。公网课程 17 门，报告最近成功采集 15:57:16、未降级，订阅页仍显示邮件暂停。

资源：MemoryCurrent=372228096、available memory 约 774 MiB、Swap 使用约 1.5 GiB，根磁盘 70%、约 12 GiB 可用。最新数据库/图片备份时间为 15:55:31，周备份仍有 4 份。最终发布、备份路径、回退与未运行邮件项目见 [发布验收](RELEASE_VALIDATION_2026_10_22.md)。以下 15:09 为发布前历史基线，不覆盖本节。

## 发布前：15:09 基线

核对窗口：北京时间 2026-10-22 15:09。来源：[只读服务器诊断 39738536963](https://github.com/2725238326/boya-agent/actions/runs/39738536963)。本次未重启生产、修改配置或发送消息。

## 服务、版本与数据

- 生产提交 `d9f3fda52b8e`，工作树 clean；服务 active/enabled，自 2026-09-20 16:44:46 起运行。
- Nginx 配置检查通过，MemoryMax=1363148800（1300 MiB）、TasksMax=250。
- 数据库 integrity_check=ok；99 条课程，最新 last_seen=2026-10-22 15:09:33。
- 配置：抓取间隔 10 分钟；email、Telegram、每日摘要、自动选课关闭；RSS 开启。
- 通知队列仅有 6079 条 succeeded/email，未见 pending/processing/failed。
- .env 文件未显式设置 EMAIL_DELIVERY_ENABLED、EMAIL_DELIVERY_SCOPE、APP_ENV、NOTIFICATION_MODE；默认 SMTP 配置项存在。该检查只读文件，不能替代有效进程配置或真实 SMTP 送达证明，未输出凭据。

## 资源与备份

- 主机内存约 1.9 GiB，available 558 MiB；Swap 使用 1640042496 字节（约 1.53 GiB），剩余约 483 MiB。
- 服务 cgroup MemoryCurrent=477872128（约 456 MiB），低于约 1300 MiB 上限；24 小时内核 OOM 筛查未返回匹配记录。
- 根文件系统使用 69%，约 12 GiB 可用。9 月快照为 55%，需要后续按实际目录清单分析增长，本次不清理。
- 周备份 4 份，最新 boya_agent-weekly-20261018.db，10899456 字节，生成于北京时间 2026-10-18 04:00:02；本次核对存在性/时间，没有执行备份恢复。
- 未发现 qrcode-*.tar.gz。升级工作流已加入图片备份，发布时仍需检查实际文件。

第一次诊断的 pgrep 统计为 2，但该命令可能匹配 SSH 父进程中的整段脚本，不作为实际 driver 数证明；后续诊断改为仅匹配 /proc/cmdline 中的可执行文件。

## 公网观察

本次 healthz 和课程接口返回 200，未授权 api/status 返回 401；课程接口返回 16 门课程、degraded=false，报告 last_success=2026-10-22 15:09:58。

健康端点和短观察窗口不能替代长期可靠性。Swap 和磁盘比 9 月更高，发布前继续复核 available memory、备份与恢复所需空间，避免盲目清理或重启。

## 候选版本

[PR #1](https://github.com/2725238326/boya-agent/pull/1) 首批提交 286621e 已通过 Linux 云端 CI：124 项测试与 24 项子测试、隔离预发布、JS/TypeScript 及全部离线浏览器场景。后续增加 Python 3.10/3.12 双版本检查和独立 systemd 服务恢复演练；最终候选以 PR 最新提交检查为准。

生产环境当前未配置 GitHub required reviewers 或部署分支策略。此处只记录事实，未修改仓库保护设置。合并 main 会自动触发部署，邮件发送范围仍需单独核对。
