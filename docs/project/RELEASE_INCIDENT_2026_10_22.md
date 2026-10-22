# 2026-10-22 发布验收时间格式兼容问题

首次运行：[Deploy 39740214011](https://github.com/2725238326/boya-agent/actions/runs/39740214011)，目标 df01337，旧版本 d9f3fda。

15:26:33 新版启动，15:26:43 本机健康通过，随后首页、订阅、门户、二维码、课程及 RSS 冒烟通过。15:30:41 的独立只读诊断确认 df01337、active、工作树 clean、1 个 Playwright driver、数据库完整性 ok、近期采集成功，外部通道仍关闭。

但部署脚本把带 T 和时区偏移的 ISO 时间传给旧版生产 journalctl，后者持续返回 Failed to parse timestamp。原先的纯本地日期格式可用；为 Python 时区比较改成 ISO 时遗漏了这项现场兼容性。等待 4 分钟后验收判失败，15:30:45 自动恢复 d9f3fda，15:30:49 HTTP 恢复通过。数据库和 outbox 保留，未恢复备份覆盖数据。

数据库备份 `/var/lib/boya-agent/backups/boya_agent-20261022-152625-pre-df01337.db`；图片备份同目录 `qrcode-20261022-152625-pre-df01337.tar.gz`（121 字节，当前目录内容较少）；恢复产物 `/var/lib/boya-agent/release-recovery.Sz0d7H`。

修复：将日志查询的 JOURNAL_SINCE 与 Python 比较的 STARTED_AT 分离；journalctl 恢复使用 `%Y-%m-%d %H:%M:%S`。独立 systemd 演练补实际 journalctl 查询。修复后通过功能分支 CI 再发布，不在服务器直接编辑业务文件。

本记录不是升级完成证明；最终运行版本和发布结论以重试发布及其后只读核对为准。该失败同时提供了实际生产自动回退的运行证据，但不能替代其他故障路径演练。
