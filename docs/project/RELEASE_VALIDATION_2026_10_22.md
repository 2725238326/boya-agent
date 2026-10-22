# 2026-10-22 升级发布验收

结论：升级代码已部署至 https://buaaboya.top，生产提交 `73e04b1daa629fff4601e4eaaeb3ad4df77b20fb`。2026-10-22 15:56:34（北京时间）工作流完成验收，随后独立只读诊断和公网检查通过。邮件、Telegram、每日摘要和自动选课继续关闭，真实邮件送达尚未验收。

## 版本和证据

- 原生产版本：`d9f3fda52b8e`；现生产工作树 clean。
- 已合并：[升级 PR #1](https://github.com/2725238326/boya-agent/pull/1)、[日志时间修复 PR #2](https://github.com/2725238326/boya-agent/pull/2)、[采集恢复验收 PR #3](https://github.com/2725238326/boya-agent/pull/3)。
- [最终生产发布 39743701685](https://github.com/2725238326/boya-agent/actions/runs/39743701685)：success。
- [发布后独立诊断 39744108169](https://github.com/2725238326/boya-agent/actions/runs/39744108169)：success，窗口 15:57。
- [最新分支 CI](https://github.com/2725238326/boya-agent/actions/runs/39743122570) 和 PR 合并检查均通过：Linux Python 3.10/3.12 各 129 项测试、28 项子测试，JS/TypeScript、全部离线浏览器场景及独立 systemd 恢复通过。

本发布记录随后以纯文档提交同步到仓库，不触发应用重启。文档提交与实际应用运行提交分别记录，不把后续仓库 HEAD 当作服务器代码。

## 发布内容与运行结果

邮件 auth/all 范围、SMTP 连接与代理处理、纯文本邮件、后台线程投递及通知租约续期已进入生产代码；本地 fixture/dry-run、旧库升级演练、部署失败恢复及诊断也随版本保存。没有修改真实环境文件、通道开关或删除数据库记录。

依赖内容哈希一致，跳过 wheelhouse 构建/跨境上传/重装；Playwright 二进制幂等核对。发布前生成并检查 SQLite 一致性备份与二维码归档。应用启动执行既有增量迁移，未新增删除式迁移。

最终启动首轮遇到临时空页，健康保护拒绝覆盖可靠数据。15:56:23 日志就绪后，本机采集检查先报告 source_degraded_or_unknown；15:56:34 在正常调度恢复后报告 ready，满足“本次启动后采集成功且未降级”。没有跳过业务验收，也没有清空课程。

15:57 独立检查：服务 active/enabled，启动时间 15:55:36；SQLite integrity_check=ok；100 条课程，最新 last_seen=15:56:46；1 个 Playwright driver。通知任务仍只有 6079 条 succeeded/email，无 pending/processing/failed。

公网 `/healthz`、首页、订阅、门户、二维码、课程、RSS、Atom 均返回 200；未授权 `/api/status` 返回 401。课程接口在 15:57:16 报告采集成功、degraded=false、17 门公开课程。订阅页显示邮件暂停且提交按钮停用，确认当前用户入口未恢复发信。

## 数据和回退

成功发布的备份：

- `/var/lib/boya-agent/backups/boya_agent-20261022-155531-pre-73e04b1.db`，10919936 字节。
- `/var/lib/boya-agent/backups/qrcode-20261022-155531-pre-73e04b1.tar.gz`，121 字节；当前上传目录内容较少，已检查归档可读。
- 恢复产物目录 `/var/lib/boya-agent/release-recovery.IxfAKl`；依赖未变，本轮不需旧 venv 快照。

本轮前两次尝试分别因旧 systemd 日志时间格式和过早判断首次抓取失败而回退；均恢复旧服务，保留数据库/outbox，详见 [发布问题记录](RELEASE_INCIDENT_2026_10_22.md)。最终发布已修复这两项。紧急回退代码以 d9f3fda 为已知旧基线，正式回退优先采用审阅后的 revert，避免恢复数据库覆盖本轮新记录。

## 资源与剩余事项

服务 MemoryCurrent=372228096（约 355 MiB），上限 1300 MiB，TasksMax=250；available memory 约 774 MiB，Swap 使用约 1.5 GiB。磁盘约 70%、剩余约 12 GiB；周备份 4 份，最新为 10 月 18 日。短期采样不代表长期稳定性，持续观察磁盘增长、Swap、浏览器数量和采集失败。

剩余工作：

1. 确认授权测试收件邮箱，限定范围验证 SMTP、收件箱/垃圾箱、验证码、一次性链接与跨设备登录；核对实际发件域的 SPF/DKIM/DMARC。尚未执行真实发送。
2. 身份邮件验收后再评审开放范围，先 auth；课程邮件和摘要另批评审旧任务、筛选、暂停/退订与重复窗口。当前不改生产开关。
3. 完成至少 24 小时运行观察并覆盖浏览器回收；本次仅完成部署窗口检查，没有创建定时监控。
4. 根据明确清单制定图片备份、恢复目录和浏览器版本的保留策略；本次未执行生产清理。

自动选课继续冻结，Telegram/飞书接入继续暂缓。冷启动偶发空页仍是既有待观察行为，健康保护与后续调度恢复已实际运行；没有宣称已根治上游空页竞态。
