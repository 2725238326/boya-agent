# 日常运维手册

文档用途：提供可复制的启动、检查、备份和故障处理步骤。
面向读者：值班维护者。
文档状态：当前运行手册；更新时间：2026-09-07。
当前生产实例：`https://buaaboya.top`，应用目录 `/home/boya-agent`，运行数据库 `/var/lib/boya-agent/data/boya_agent.db`。其他环境执行命令前仍需核对域名、路径和凭据。

## 服务控制

```bash
sudo systemctl status boya-agent --no-pager
sudo systemctl restart boya-agent
sudo systemctl stop boya-agent
sudo systemctl start boya-agent
sudo systemctl status nginx --no-pager
sudo nginx -t && sudo systemctl reload nginx
```

## 常规更新后的最低检查集

每次代码更新后记录以下信息，不以“服务能启动”代替完整验收：

```bash
cd /home/boya-agent
git branch --show-current
git rev-parse --short HEAD
git status --short
sudo systemctl is-active boya-agent
curl -fsS https://buaaboya.top/healthz
curl -fsS https://buaaboya.top/api/courses
```

随后查看最近启动和抓取日志，确认没有迁移异常或连续启动失败：

```bash
sudo journalctl -u boya-agent -n 80 --no-pager
```

课程为空时，`/api/courses` 返回空列表可以是正常业务状态；应结合日志中的结构化抓取状态判断是“成功无课”还是“抓取失败”，不能因为页面没有课程就手动清空数据库。

看最近日志：

```bash
sudo journalctl -u boya-agent -n 200 --no-pager
sudo journalctl -u boya-agent -f
sudo tail -n 200 /home/boya-agent/logs/boya_agent_$(date +%F).log
```

日志应能说明抓取模式、健康检查、保存数量、推送渠道和失败原因；邮箱只应以脱敏形式出现，不能出现完整 token、密码或 Cookie。

## 抓取与后台检查

先做不带管理员凭据的服务探活：

```bash
curl -i https://你的域名/healthz
```

该端点只验证应用和 SQLite 是否可响应；需要内部统计时再使用管理员接口：

后台认证后查看完整状态：

```bash
curl -su 管理员用户名:管理员密码 https://你的域名/api/status
```

手动触发后台抓取：

```bash
curl -su 管理员用户名:管理员密码 \
  -H 'Content-Type: application/json' \
  -X POST 'https://你的域名/api/trigger?mode=quick'
```

`mode=quick` 适合快速刷新；完整同步使用 `mode=full`。重复触发时，应用会尽量加入已有任务，而不是并行启动同一条抓取流程。检查状态中的 `last_run`、`last_success`、`last_error`、`browser_alive`、`last_scrape_health` 和 `last_scrape_duration_ms`。单次耗时只用于定位异常，长期性能应按观察窗口统计。

## 邮件与推送检查

测试邮件接口只接受管理员请求：

```bash
curl -su 管理员用户名:管理员密码 \
  -H 'Content-Type: application/json' \
  -d '{"to":"自己的测试邮箱"}' \
  -X POST https://你的域名/api/test-email
```

课程推送是否自动发送由数据库中的 `FilterConfig.email_enabled`、`telegram_enabled`、`daily_summary_enabled` 控制；这些开关默认关闭。手动推送接口是管理员明确要求的邮件发送动作，不能把它当作自动通道开关的健康检查。

课程邮件已经接入持久化 outbox。日志中的主 SMTP 超时不等于任务立即丢失，应先查看 `notification_jobs` 的状态汇总；只有任务进入 `failed` 且达到重试上限，才需要按收件人范围和失败原因进一步处理。不要为了验证通道而重复触发整批手动推送。

查看推送记录：

```bash
curl -su 管理员用户名:管理员密码 https://你的域名/api/logs/push
curl -su 管理员用户名:管理员密码 https://你的域名/api/logs/enroll
```

查看通知任务汇总时只输出状态和数量，不输出邮箱、Bot token 或 payload：

```bash
sudo sqlite3 /var/lib/boya-agent/data/boya_agent.db \
  "SELECT status, channel, COUNT(*) FROM notification_jobs GROUP BY status, channel ORDER BY status, channel;"
```

如果服务器没有 `sqlite3` 命令，使用项目虚拟环境中的只读 Python/SQLAlchemy 查询；不要为检查方便把生产数据库复制进仓库。

## 数据库备份与恢复

实际位置由 `DATABASE_PATH` 决定；当前生产数据库是 `/var/lib/boya-agent/data/boya_agent.db`。先查看 `.env` 和运行目录，确认目标是单个明确的数据库文件，再备份：

```bash
sudo systemctl stop boya-agent
sudo cp -p /var/lib/boya-agent/data/boya_agent.db /safe/backup/path/boya_agent-$(date +%F-%H%M).db
sudo systemctl start boya-agent
```

恢复前停止服务，把已验证的备份复制回实际 `DATABASE_PATH`，再启动并查看日志。不要把示例路径 `/safe/backup/path` 原样执行，也不要对工作区根目录使用递归删除或覆盖命令。

二维码文件也要单独备份：

```bash
sudo tar -C /home/boya-agent -czf /safe/backup/path/boya-qrcode-$(date +%F-%H%M).tar.gz config/uploads/qrcode
```

## 清理过期课程

管理员可以调用：

```bash
curl -su 管理员用户名:管理员密码 \
  -H 'Content-Type: application/json' \
  -d '{"days":30}' \
  -X POST https://你的域名/api/cleanup-expired
```

该接口按课程的报名结束时间清理已标记过期且超过指定天数的记录。先确认备份和天数，再执行；它不是二维码文件清理器，也不会替代数据库备份。

## 常见故障

### 服务启动后立即退出

1. 看 `journalctl -u boya-agent` 的第一条配置错误。
2. 确认 `WEB_SECRET_KEY` 至少 32 个字符。
3. 确认已配置管理员 Bearer token，或同时配置 Basic 用户名和密码。
4. 确认 `.env` 可由 `boya-agent` 读取，且数据库/日志目录可写。
5. 确认依赖和 Chromium 已安装。

### 首页正常但邮件链接打不开

检查 `APP_PUBLIC_BASE_URL` 是否为真实 HTTPS 地址，证书是否有效，Nginx 的 `server_name`、证书路径和 `X-Forwarded-Proto` 是否正确。不要把 `127.0.0.1:5000` 写入邮件基址。

### 验证码无效或重复使用

验证码只有短时间有效，且成功使用或达到错误次数上限后失效。重新发送新邮件；查看应用日志时只核对脱敏邮箱和失败阶段，不要记录或要求用户发送完整验证码。

### 抓取失败

确认北航账号仍可用、主机可访问 WebVPN/课程系统、Chromium 可启动；检查 `last_scrape_health`、截图和日志。连续失败达到阈值时，Telegram 告警还必须满足 Telegram 通道已开启并配置凭据。自动选课当天失败达到 `AUTO_ENROLL_FAILURE_LIMIT` 后会熔断本轮后续尝试；恢复前先确认账号、选课页面和错误原因。

### SQLite 锁竞争

先确认是否误启动了第二个服务实例。当前设计是单实例 SQLite，并对短暂锁竞争做有限重试；不要通过复制多个 systemd 实例来扩容。若持续发生，先停止服务、备份数据库并检查磁盘和进程。

### 二维码上传成功但列表没有

上传成功只代表记录进入 `pending`；必须由管理员审核为 `approved`，且对应课程未过期、文件仍 active，公开列表和文件地址才会可见。
