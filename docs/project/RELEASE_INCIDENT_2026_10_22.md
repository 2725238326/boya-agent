# 2026-10-22 发布验收时间格式兼容问题

首次运行：[Deploy 39740214011](https://github.com/2725238326/boya-agent/actions/runs/39740214011)，目标 df01337，旧版本 d9f3fda。

15:26:33 新版启动，15:26:43 本机健康通过，随后首页、订阅、门户、二维码、课程及 RSS 冒烟通过。15:30:41 的独立只读诊断确认 df01337、active、工作树 clean、1 个 Playwright driver、数据库完整性 ok、近期采集成功，外部通道仍关闭。

但部署脚本把带 T 和时区偏移的 ISO 时间传给旧版生产 journalctl，后者持续返回 Failed to parse timestamp。原先的纯本地日期格式可用；为 Python 时区比较改成 ISO 时遗漏了这项现场兼容性。等待 4 分钟后验收判失败，15:30:45 自动恢复 d9f3fda，15:30:49 HTTP 恢复通过。数据库和 outbox 保留，未恢复备份覆盖数据。

数据库备份 `/var/lib/boya-agent/backups/boya_agent-20261022-152625-pre-df01337.db`；图片备份同目录 `qrcode-20261022-152625-pre-df01337.tar.gz`（121 字节，当前目录内容较少）；恢复产物 `/var/lib/boya-agent/release-recovery.Sz0d7H`。

修复：将日志查询的 JOURNAL_SINCE 与 Python 比较的 STARTED_AT 分离；journalctl 恢复使用 `%Y-%m-%d %H:%M:%S`。独立 systemd 演练补实际 journalctl 查询。修复后通过功能分支 CI 再发布，不在服务器直接编辑业务文件。

本记录不是升级完成证明；最终运行版本和发布结论以重试发布及其后只读核对为准。该失败同时提供了实际生产自动回退的运行证据，但不能替代其他故障路径演练。

## 第二次验收：冷启动恢复窗口

重试运行 [39742138110](https://github.com/2725238326/boya-agent/actions/runs/39742138110) 的日志时间兼容性已修复，健康/页面/日志检查通过；但首次抓取读到临时空页，健康保护发现 db_active=17、min_expected=5 后拒绝覆盖旧数据。发布即时检查 last_success 未成功，于 15:43:36 自动恢复旧版。

[启动窗口只读诊断](https://github.com/2725238326/boya-agent/actions/runs/39742715853) 显示：旧版恢复后首轮同样遇到临时空页；15:44:23 开始 quick 轮，15:44:33 正常解析并更新 15 条课程，后续持续成功。因此该现象不是本次邮箱改动特有回归，既有调度可以恢复；发布检查应给调度有限恢复时间。

修复增加 check_startup_source.py：在最多 240 秒内查询本机实时 source，必须同时满足请求成功、degraded=false、成功采集时间不早于本次启动。允许短暂 HTTP 失败及抓取重试；过期时间、未知 source 和持续失败均不得通过，超时仍回退。使用本机请求避免公开缓存影响，并保留独立公网冒烟检查。新增 5 项语义测试覆盖恢复、持续失败、旧时间、HTTP 异常和合法空课程。
