# 日常运维手册

文档用途：提供可复制的启动、检查、备份和故障处理步骤。
面向读者：值班维护者。
文档状态：当前运行手册；更新时间：2026-10-24。
当前生产实例：`https://buaaboya.top`，应用目录 `/home/boya-agent`，运行数据库 `/var/lib/boya-agent/data/boya_agent.db`。运行版本和通道状态见 [项目状态](../project/PROJECT_STATUS.md)，最近应用验收见 [采集与学生卡片](../project/SCRAPE_CARDS_RELEASE_2026_10_24.md)。其他环境执行命令前核对域名、路径和凭据。

## 服务控制

```bash
sudo systemctl status boya-agent --no-pager
sudo systemctl restart boya-agent
sudo systemctl stop boya-agent
sudo systemctl start boya-agent
sudo systemctl status nginx --no-pager
sudo nginx -t && sudo systemctl reload nginx
```

## 当前测试期的外部通知策略

飞书学生课程卡片已启用，运维消息不进入飞书。Telegram 独立运维按用户决定待网络打通后启用；旧 Telegram 课程开关、邮件、每日摘要和自动选课仍关闭。邮件还受 `EMAIL_DELIVERY_ENABLED` 进程总开关保护，目前暂停；测试期不要把邮箱验证/登录、管理员测试邮件、手动推送或站点群发当作只读检查。配置与真实验收分别按 [飞书](FEISHU.md)、[Telegram 运维](TELEGRAM_OPS.md) 执行。

恢复邮件前记录授权收件范围、旧值和回退方式，按本手册的邮件检查先验证真实指定邮箱，再分阶段开放 auth/all。历史暂停备份只用于追溯，恢复操作使用已核对的近期备份。

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

### SSH 不响应时的主机诊断

如果 22 端口能建立 TCP 连接但 SSH banner、HTTPS 或本机健康检查均无响应，先在腾讯云 VNC/串口终端执行仓库中的 `deploy/host_health.sh`。脚本只读输出主机内存、Swap、负载、占用最高的进程、监听端口、服务状态、OOM 记录和近期抓取日志；不要在 SSH 未恢复时反复触发部署。

判断内存问题时以 `free -h`、`swapon --show` 和内核 OOM 记录为准。Playwright Chromium 与应用同机运行，生产实例建议至少 2 GiB 内存并配置 1–2 GiB Swap；若实例低于此规格或出现 `oom-killer`/`Killed process`，先扩容或补 Swap，再重启 `sshd`、`nginx` 和 `boya-agent`，最后重新执行部署和抓取核验。

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

quick 轮会顺手为两类课程补抓详情页：库里缺少签到方式等详情字段的课程，以及当轮新发现的课程；单轮上限由 `QUICK_DETAIL_ENRICH_LIMIT`（默认 8）控制，超出的留到后续轮次。full 轮同样只点开这两类课程而不是逐课程点击；补抓上下文装载失败时 full 轮退化为全量详情抓取，功能不受影响。课程签到标签显示"待确认"表示详情页尚未读取，不是识别错误；若某门课长期停留在该状态，先确认详情页本身是否提供签到方式字段，以及该课程是否仍在当前被抓取的视图中。

如果日志出现周期性的 `scrape returned no courses and was blocked as suspicious` 且间隔与浏览器回收周期（约 48 次抓取）一致，这是浏览器重建后首轮的空状态竞态（已修复为二次确认）。若修复后仍出现，按真实抓取失败排查上游页面结构。

## 邮件与推送检查

2026-10-22 新版本支持先开放身份邮件：EMAIL_DELIVERY_ENABLED=true + EMAIL_DELIVERY_SCOPE=auth。该范围允许验证和登录，但拒绝管理员测试邮件、手动课程推送和站点群发，email outbox 不领取、不消耗重试。关闭总开关即可停止全部邮件。打开 all 前需要核对数据库渠道开关与历史任务，不能把 auth 改 all 当作无副作用操作。

管理认证后的 /api/status 提供 mail.auth_allowed、mail.notifications_allowed、传输配置是否齐全和 notification_jobs 状态计数。配置齐全不是送达证明；真实发送只对已授权收件范围进行。生产恢复前核对 SPF/DKIM/DMARC、SMTP 响应、收件箱及垃圾箱、验证码/链接有效性与暂停/退订行为。

本地使用 scripts/run_staging.py 生成 .eml；scripts/verify_staging.py 会阻断真实连接并自动演练失败恢复。不得将文件发送成功记录为生产送达。

测试邮件接口只接受管理员请求：

```bash
curl -su 管理员用户名:管理员密码 \
  -H 'Content-Type: application/json' \
  -d '{"to":"自己的测试邮箱"}' \
  -X POST https://你的域名/api/test-email
```

课程推送是否自动发送由数据库中的 `FilterConfig.email_enabled`、`telegram_enabled`、`daily_summary_enabled` 控制，并且邮件还必须通过 `EMAIL_DELIVERY_ENABLED` 总开关；这些开关默认关闭。手动推送接口是管理员明确要求的邮件发送动作，不能把它当作自动通道开关的健康检查。

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

实际位置由 `DATABASE_PATH` 决定；当前生产数据库是 `/var/lib/boya-agent/data/boya_agent.db`。日常使用 SQLite 在线备份以包含 WAL 中已提交数据，不直接复制活跃数据库文件。先确认实际路径和已存在的私有备份目录：

```bash
sudo sqlite3 /var/lib/boya-agent/data/boya_agent.db ".backup '/var/lib/boya-agent/backups/boya_agent-manual-$(date +%Y%m%d-%H%M%S).db'"
```

备份后对刚生成的文件执行 `PRAGMA integrity_check` 并核对权限。没有 sqlite3 CLI 时使用发布工作流同样的 Python sqlite3.backup，不为检查复制原 DB。恢复应另建明确清单：停止服务、保存当前 DB/WAL/SHM、恢复已验证的备份、处理旧 sidecar、修正权限，再启动核对迁移、outbox 与真实采集。不要仅替换 `.db` 留下旧 WAL/SHM。

除部署时备份外，生产机还有每周日凌晨 4 点的 cron 快照：`sqlite3 .backup` 写入 `/var/lib/boya-agent/backups/boya_agent-weekly-YYYYMMDD.db`，只保留最近 4 份。该快照在服务运行中用 SQLite 在线备份完成，不需要停服。

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

确认北航账号、WebVPN/课程系统与 Chromium 状态，检查 `last_scrape_health`、分页覆盖、错误截图和日志。失败/恢复交给独立 `telegram_ops` 通道，带阈值和冷却；不受旧 Telegram 课程开关控制，也不发送飞书运维告警。当前网络未打通，运维通道和 timer 保持关闭，因此不能依赖 Telegram 已通知。启用条件和主进程外检测见 [TELEGRAM_OPS.md](TELEGRAM_OPS.md)。

## 变更后监测清单

每次发布后记录观察窗口，以下项目不能用一次 healthz 代替：

| 观察项 | 正常表现 | 检查方式 |
| --- | --- | --- |
| 空状态误报 | 浏览器回收（日志 `browser recycled after 48 runs`）后的首轮不再出现 `blocked as suspicious` | `journalctl -u boya-agent --since today \| grep "blocked as suspicious"` |
| full 轮定向详情 | full 轮日志 `开始抓取 N 门课程的详情` 中 N 明显小于课程总数；无缺口时不出现该行 | `journalctl -u boya-agent \| grep "开始抓取"` |
| 陈旧行 | 列表名额标记待核对、群推跳过陈旧数据；长期未命中且管道健康时软过期 | 各课 `last_seen`、页面名额标签、生命周期日志 |
| 分页覆盖 | 页数/唯一记录数有日志；循环、异常空页和翻页失败不会作为完整快照保存 | 日志 `Course coverage`、`Course pagination`；独立上游审计按需运行 |
| 通道分工 | 飞书只有课程；Telegram 运维需联网后单独启用，告警/恢复与冷却有记录 | 实际配置、outbox 状态、群内验收；当前不期待 TG 消息 |
| 周更备份 | 每周日 04:00 后 `/var/lib/boya-agent/backups/` 出现 `boya_agent-weekly-*.db`，总数不超过 4 份 | `ls -1t /var/lib/boya-agent/backups/` |
| 资源水位 | run-driver 进程稳定为 1 个；`available` 内存长期不低于 ~300Mi | `pgrep -fc run-driver`、`free -m` |

## 目录和诊断产物维护

按 [DIRECTORY_MAINTENANCE.md](DIRECTORY_MAINTENANCE.md) 先盘点，再归档已确认的旧捕获。维护工作流检查原进程和应用提交保持不变；归档保留摘要和恢复清单。日志/上传的活跃路径、数据库、配置、venv 和 Chromium 不纳入通用搬迁。

### SQLite 锁竞争

先确认是否误启动了第二个服务实例。当前设计是单实例 SQLite，并对短暂锁竞争做有限重试；不要通过复制多个 systemd 实例来扩容。若持续发生，先停止服务、备份数据库并检查磁盘和进程。

### 二维码上传成功但列表没有

上传成功只代表记录进入 `pending`；必须由管理员审核为 `approved`，且对应课程未过期、文件仍 active，公开列表和文件地址才会可见。
