# Telegram 运维检测

用途：管理员接收采集异常、恢复、应用存活、采集时效与磁盘告警。飞书保留同学课程群用途。需求见 [通知需求](../product/NOTIFICATION_REQUIREMENTS.md)，本轮结果见 [发布验收](../project/SCRAPE_CARDS_RELEASE_2026_10_24.md)。

## 配置

`TELEGRAM_OPS_ENABLED=true` 独立启用运维，不依赖数据库的 `telegram_enabled` 课程开关。可配置独立 `TELEGRAM_OPS_BOT_TOKEN` / `TELEGRAM_OPS_CHAT_ID`；两项都空时复用原有 Telegram 凭据，不能只提供独立凭据的一半。`TELEGRAM_OPS_PROXY_URL` 支持显式 HTTP/HTTPS 代理；未设置时沿用 HTTPS_PROXY / HTTP_PROXY，不支持的代理不会静默绕过。真实值只放受控环境文件或 Secrets。

生产配置工作流先从服务器发送验收消息，验证实际通道成功，再备份和更新环境。失败不激活；重启/验收失败恢复旧环境。以下命令在服务器与工作流提交精确一致且 clean 时运行：

```powershell
gh workflow run configure-feishu.yml --ref main -f alerts_enabled=false -f courses_enabled=true -f ops_enabled=true
```

工作流强制飞书告警关闭。Telegram 凭据继续保留在服务器，不会写入命令行参数或工作流日志。邮件、课程 Telegram、每日摘要与自动选课保持原值。

## 独立检测

部署 `boya-agent-ops.service` / `.timer`，当前模板针对 `/home/boya-agent` 安装。每轮脚本 `scripts/check_ops_health.py` 在主应用进程之外运行：本地健康检查失败、在报名课程超过 15 分钟无成功采集或 source degraded、运行盘使用率至少 90% 时累计失败；连续 3 次后排队告警。没有在报名课程时不要求高频采集。健康时仅本地记结果；恢复通知幂等，不定时发“正常”消息。

状态保存在 `/var/lib/boya-agent/ops-watchdog.json`，通知进入 `channel=telegram_ops` 的持久任务，按目标指纹、故障编号和组件隔离。三次投递尝试及现有退避；故障恢复后未发送的旧告警跳过，两小时以上任务跳过。不保存原始错误、账号、Chat ID 或 token 到任务。外部接受与落库之间仍存在至少一次投递窗口。

诊断：管理员 `/api/status` 的 `telegram_ops` 返回是否配置、是否启用和用途；只读 Server Diagnostics 显示安全计数。可用 `systemctl status boya-agent-ops.timer`、`journalctl -u boya-agent-ops.service` 检查定时器。

关闭运维时以同一工作流 `ops_enabled=false` 更新，它会停止并禁用 timer；飞书课程可继续开启。主机断电/断网、数据库不可用和 Telegram 网络不可达仍需外部监控或人工核对，不能承诺本机检测覆盖所有故障。

## GitHub 邮件

GitHub Actions 失败邮件来自 GitHub 工作流通知，不代表项目正在发送课程邮件。Server Diagnostics 只核对生产状态；需要额外登录校园页面的人工审计已拆到 `Upstream Pagination Audit`，仅按需手动运行。该审计超时会如实失败，但不改变生产数据库、通道或应用代码。
