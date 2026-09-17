# BOYA Agent 当前项目状态

## 2026-09-17 巡检与加固批次：空状态误报修复 + 陈旧行过期

**巡检发现与修复（`ecc3274`/`11d9473` 已部署；陈旧行过期见下）**：

**现象**：今日 14:53 起，日志每约 28 分钟出现一次 `scrape returned no courses and was blocked as suspicious: db_active=10, min_expected=5`，quick 轮同时段全部正常。

**根因**：浏览器每 48 次抓取软回收重建 → 重新登录后首轮流经"首页菜单点击选择课程"路径 → SPA 路由跳转滞后于点击，`page.url` 仍是首页 → `_wait_course_tables_ready` 在半渲染页面上命中"暂无数据"占位 → 后续 `_course_page_has_empty_state` 在 URL 刚跳到选课页、数据尚未加载的瞬间误判为空 → 本轮按空状态返回 [] → 健康检查拦截（防误覆盖）+ 计入失败。回收间隔（48 轮 × 约 30s ≈ 28 分钟）与错误间隔完全吻合；该竞态一直存在，只是只有热点巡检驱动频繁回收时才显现。

**修复**：① 菜单点击后先 `wait_for_url` 等 SPA 落到选课页，仍未进入则直接 `goto` 重试；② 空状态判定改为二次确认——首次判定为空后等网络空闲+静置 800ms 复核，两次皆空才算真空。新增 3 项回归测试（瞬时误判被纠正、持续空状态仍确认、非空不触发复核）。

**影响评估**：每次误报只损失一轮抓取（约 30 秒后下一轮自动恢复），未造成数据覆盖或错误展示——健康检查拦截机制按设计工作。告警阈值 3 次未触及，未产生误告警。

**同批新增**：`_sync_course_lifecycle` 增加陈旧行过期——连续 `STALE_COURSE_EXPIRE_DAYS`（默认 3 天）未被上游命中的课程在抓取管道近 1 小时有成功时标记过期；行若重新出现由 `save_courses_to_db` 重算 `expired` 自动复活，属于可自愈的软过期。正念沙龙两条幽灵行（`last_seen` 停在 09-11）预计部署后下一轮生命周期同步即被标记过期，不再等 09-23/24 报名截止。告警冷却期内新增 INFO 日志"告警冷却中"，持续故障在日志中保持可见。监测清单已写入 RUNBOOK"变更后监测清单"一节。

**本地验证（已运行）**：`pytest` 99 项全过（新增 2 项：管道健康时陈旧行过期、管道无近期成功时不清）。

## 2026-09-16 运维加固批次：告警冷却、full 轮定向详情、周更备份（本地验证通过）

**改动内容**：
- Telegram 故障告警加冷却与恢复通知：原实现每 3 次失败发一条后清零计数，quick 轮持续故障下约每 90 秒一条会刷屏；现改为冷却 `SCRAPE_ALERT_COOLDOWN_MINUTES`（默认 60 分钟）内不重复发送、失败计数持续累积，恢复成功时补发一条恢复通知。
- full 轮详情抓取改为定向：只点开"库中缺签到方式"和"当轮新发现"的课程，不再逐课程 N+1；上下文装载失败时退化为原有全量详情抓取。复用 quick 轮同一套 `detail_ids`/`known_ids` 机制，full 轮不受 attempted 去重和单轮上限约束，保持缺口重试能力。
- 生产机 crontab 新增每周日 04:00 SQLite 在线备份（`.backup`，不停服），保留最近 4 份周快照；已实测 `integrity_check ok`。
- 重复行审计：生产库 active 课程中仅 1 组同课同时段重复（正念沙龙 09-24 场，地点字段解析漂移所致），其陈旧行将于报名截止（09-23）由生命周期同步自动过期。

**已确认无需改动**：CI verify 阶段已运行 `verify_portal_browser.py`/`verify_home_browser.py` 离线浏览器检查；Telegram 失败告警机制（`send_status_message` + 阈值）本已存在，本次只补冷却与恢复语义。

**本地验证（已运行）**：`pytest` 96 项全过（新增 5 项：告警冷却、累计计数、恢复通知、full 定向目标、无上下文回退）。

## 2026-09-16 公开首页与门户展示优化（本地验证通过，待生产复核）

**改动内容**：
- 首页课程卡片把“签到方式”从折叠详情提到卡片正面，不再要求展开才能看到；报名状态按“可报名 / 未开选 / 已满 / 已结束”分色（绿 / 蓝 / 琥珀 / 灰），可报名且剩余名额 ≤3 时名额数字高亮。
- Hero 标题加 `word-break: keep-all` 与 `text-wrap: balance`，修正中文断词换行。
- 门户修复一处残留不一致：`getPortalCourseCheckInLabel` 原先用 `sign_method`（选课方式）参与判断且空值回退为“常规签到”，已改为只看 `check_in_method`、空值显示“待确认”，与后端和首页语义一致。
- 门户签到徽章新增“待确认”专用样式（琥珀虚线），与“常规签到”（实心橙）视觉区分。

**本地验证（已运行）**：`npm run check`（JS 语法 + tsc）通过；`pytest` 91 项全过；`verify_home_browser.py --screenshots` 双宽度通过并截图确认；`verify_portal_browser.py` 10 项全过。

**待验证**：部署后生产页面实际渲染效果与 `/api/courses` 字段一致性。

**部署复核（已完成）**：`949b599` 经 GitHub Actions 部署成功（依赖未变走哈希跳过路径，全流程自动完成无人工介入）。生产 `/api/courses` 返回 9 门：自主签到 4、常规签到 3、待确认 2；新静态资源已生效。两条“待确认”为 09-11 入库、`last_seen` 停留在当天的正念沙龙场次——上游列表已不再出现这两行，quick 补抓无法命中不在当前页的 dormant 行（设计内行为）；其报名截止后自然过期退出列表，详情见 IMPROVEMENT_BACKLOG 陈旧行条目。

## 2026-09-16 事故复盘与恢复部署：主机假死根因已定位并修复

**故障现象**：`buaaboya.top` 全端口表现为 TCP 握手成功但应用零响应（SSH 无 banner、HTTPS 无 ServerHello、HTTP 零字节），内核存活而用户态全部饿死；每次强制重启后 6-10 分钟内复发。

**根因**：Playwright 包已升级到 1.62.0（`constraints.txt` 锁定，需要 `chromium_headless_shell-1234`），但生产机浏览器停留在 1208。`create_browser_context` 中 `pw.start()` 成功拉起 node driver（每个 ~120MB）后 `chromium.launch` 抛异常，driver 进程泄漏；热点巡检每 15 秒一轮、每轮泄漏 2 个 driver，约 240MB/分钟耗尽 1.9GB 内存。内核日志无 OOM 记录，属于内存耗尽导致的系统级假死而非 OOM kill。

**处置与加固（已完成，生产实测）**：
- 经 npmmirror 镜像（`PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`）安装 `chromium-1234`/`chromium_headless_shell-1234`；官方 CDN 实测仅 ~56KB/s 不可用。
- `boya-agent.service` 增加 `MemoryMax=1300M`、`TasksMax=250`（drop-in override），cgroup 级 OOM 只影响本服务；`Restart=always` 保证自愈。
- SSH 加固：`PasswordAuthentication no`、`PermitRootLogin prohibit-password`，密钥登录不受影响（当时正被 Azure IP 爆破密码）。
- journald 加 `SystemMaxUse=500M`，日志目录由 3.9G 收敛至 393M；清理 `/root/.cache/ms-playwright` 622M 陈旧浏览器。
- 代码修复 `9fe0287`：`create_browser_context` 失败路径补 `pw.stop()` 杀干净 driver；`_ensure_browser` 增加 30s→600s 指数退避，持久故障下不再每 15 秒拉起新 driver。
- 部署流程修复：`deploy.yml` 在停服务后新增 `playwright install chromium` 同步步骤（同样走镜像源），包版本与浏览器二进制不再错配。

**本次部署方式说明**：CI verify 已通过；deploy 作业在 wheelhouse SCP 上传阶段再次实测为不可用的极低带宽（GitHub 云端 → 腾讯云入境 ~29KB/s），已取消该 run。因本次变更不含依赖变动（requirements/constraints 未改），改由 SSH 手动执行同一套流程：工作树 clean 检查 → `git merge --ff-only origin/main` 到 `9fe0287` → `compileall` → SQLite 一致性备份（integrity ok）→ 停服 → `playwright install` 幂等核对 → 起服 → 本机与公网 healthz/smoke 全通 → 首次抓取完成（登录成功、空课正常路径、浏览器回收阈值 24/48 生效）。wheelhouse 跨国上传瓶颈仍为已知阻塞项，建议后续在依赖未变化时跳过上传。

**同批次追加**：`c2422b6` 修复签到标签误导——`check_in_method` 为空（课程由 quick 巡检首次发现、尚未经详情页抓取）时，公开标签由"常规签到"改为"待确认"；"仅自主签到"过滤行为不变（待确认仍不算自主）。已随同一手动部署上线，45 项相关测试通过。注意：quick 轮次新发现的课程在下一轮 full 抓取前显示"待确认"，详情数据补齐后自动变为真实签到方式。

## 2026-09-16 跟进修复：详情数据补齐提速与空值回写防护

**起因**：同日发现同一门课两个场次签到标签不一致——14:30 场显示"常规签到"、19:00 场显示"自主签到"。排查确认 14:30 场由 quick 巡检首次发现，`check_in_method` 为空，旧标签逻辑把"未抓取详情"误显示为"常规签到"（已由 `c2422b6` 改为"待确认"）。上游详情页证实两场均为自主签到。

**本批次改动**：
- quick 巡检新增定点详情补抓：每轮为"库中缺 `check_in_method` 的课程"和"当轮新发现（id 不在库中）的课程"点开详情页，单轮上限 `QUICK_DETAIL_ENRICH_LIMIT`（默认 8），进程内已尝试过的 id 记入 `_detail_attempted_ids` 防止详情页本身无签到字段的行被高频重开。新课程获得真实签到标签的等待时间由最长一个 full 周期（720 分钟）缩短到通常一两轮 quick 巡检。
- `save_courses_to_db` 字符串字段统一改为"空值不覆盖"：此前网络层快照携带的空 `check_in_method`/`description`/`organizer`/`sign_method` 会把详情页已补到的值擦除为空白，属隐藏数据丢失路径。
- `deploy.yml` 备份步骤新增保留策略：数据库备份只保留最近 30 份，避免长期无限增长。
- 回归测试新增：空值不覆盖、定点补抓目标选择（缺详情 id ∪ 新 id）、`_detail_attempted_ids` 去重与单轮上限、`_load_detail_enrich_context` 查询。本地全套测试 91 项通过。

**待部署验证**：改动为纯后端逻辑，部署后观察日志中 quick 轮"开始抓取 N 门课程的详情"是否只在有缺口时出现、`/api/courses` 中新课程的签到标签是否在数分钟内由"待确认"变为真实值。

**部署验证结果（同日实测）**：手动部署 `1aee906` 后，quick 轮日志出现"开始抓取 1 门课程的详情 → 签到方式: 自主签到"，下一轮无缺口自动跳过；行为符合设计。同步完成服务器目录整理：`/home/boya-agent` 下 9 月 2 日遗留的旧库文件（db/shm/wal）移入 `backups/legacy/`，logs/ 清理 7 天前调试截图约 13MB，磁盘占用由 61% 降至 50%。

**流水线修复**：`deploy.yml` 新增依赖哈希跳过——`requirements.txt + constraints.txt` 内容哈希与服务器 `.deps-sha256` 标记一致时，跳过 wheelhouse 构建、SCP 上传和离线安装（上传实测 ~29KB/s 是连续三次部署 run 被取消的根因）。服务器 venv 异常时自动回退完整安装。已手动写入当前标记 `0db53b34…`，下一次推送即可实测跳过路径。

## 2026-09-16 部署状态：等待服务器连接恢复（历史记录）

补充实测：提交 `10a14c1` 增加上传前 15 秒 SSH 握手检查。[云端诊断 35008911678](https://github.com/2725238326/boya-agent/actions/runs/35008911678) 于北京时间 2026-09-16 02:40 显示 TCP 已连接，但等待 SSH banner 超时；本机探测结果相同。不能再将上传停留直接归因于带宽慢。需通过腾讯云控制台检查主机资源和 sshd，恢复连接后再部署。检查脚本已验证正常 banner、连接关闭和超时三条路径。

[网络探测 35010850953](https://github.com/2725238326/boya-agent/actions/runs/35010850953) 从 GitHub 云端确认 22、80、443 端口均可建立 TCP 连接，但 HTTPS TLS 握手同样超时。现有证据支持“主机或用户态服务无响应”，尚不能证明内存不足；需在控制台查看 `free -h`、Swap、`dmesg`/`journalctl -k` 中的 OOM 记录，以及 sshd 状态。

复测 [35012167974](https://github.com/2725238326/boya-agent/actions/runs/35012167974) 结果相同；本机复测亦为 TCP 22/80/443 可达、SSH banner 和 HTTPS 超时。服务器尚未恢复到可部署状态。

重启后曾短暂恢复，随后再次出现同样症状；这提高了“运行期资源累积或用户态进程退化”的可能性，但仍需控制台的内存、Swap、负载和 OOM 数据确认。提交 `f12eb58` 已将 Playwright 浏览器默认回收阈值收紧为 24/48 轮，完整验证通过；[部署 35012555316](https://github.com/2725238326/boya-agent/actions/runs/35012555316) 因 SSH 预检失败而未安装该版本。

仓库已完成 uv 迁移及自动选课结果 outbox 接入；`c280d9c` 的云端发布验证通过，Linux CPython 3.10 离线依赖包构建成功。部署改为先打包、SCP 上传、服务器通过 uv 离线安装。运行 [35006272639](https://github.com/2725238326/boya-agent/actions/runs/35006272639) 长时间停留在上传阶段，尚无成功部署证据。

本机直连生产服务器的 SSH 在 banner exchange 阶段超时；只读诊断运行 [35007525726](https://github.com/2725238326/boya-agent/actions/runs/35007525726) 也未返回运行状态。当前课程抓取状态**未确认**，不能从部署失败推断抓取已停止，也不能把历史生产验证视为当前健康证明。连接恢复后先检查 systemd 服务、近期抓取日志及数据库完整性，再继续离线部署。下文发布记录均为历史证据。

## 最新发布：公开课程浏览

2026-09-08 应用更新至 `990d404`。首页无需登录浏览和筛选课程，个人操作仍受会话保护；页面明确显示邮件暂停及采集时间。生产版本的发布验证已通过；当前收尾工作树使用 uv 完成 `85 passed, 9 subtests passed`、依赖检查、前端检查和编译检查。具体范围与剩余限制见 [公开浏览发布记录](PUBLIC_BROWSING_2026_09_08.md)。以下为此前批次记录。

## 最新发布：门户恢复与移动端修复

2026-09-08 应用已更新至 `e72589b`，生产工作流和上线检查通过。修复失效页签、登录状态加载失败、已满课程键盘操作及窄屏溢出；Python 3.12 隔离环境中 71 项测试通过，无跳过项，八组离线浏览器场景通过。邮件仍关闭。详细证据和限制见 [门户发布记录](PORTAL_RELEASE_2026_09_08.md)。下文为此前批次记录。

## 2026-09-08 更新

生产应用已更新至 `0b44182`：邮件总开关默认关闭，覆盖验证码及管理端发送；健康检查、服务状态和暂停接口已在生产复核。邮件、摘要、Telegram、自动选课四项配置均为关闭，队列只有 6079 条历史成功任务，没有待领取任务。下文早期版本记录保留为背景，最新验证范围与回退限制见 [发布验证记录](RELEASE_VALIDATION_2026_09_08.md)。

文档用途：回答“当前项目是什么、哪些能力可用、哪些风险未确认”。
面向读者：项目负责人、开发者、运维人员和评审者。
文档状态：当前状态主文档；更新时间：2026-09-07。

判定范围：仓库代码、配置样例、部署样例、测试文件，2026-09-02 至 2026-09-04 对生产主机的部署、真实课程抓取和通知观察结果，以及 2026-09-07 完成的版本发布和线上核对。当前生产运行代码为 `main@92e53b2`；服务器仓库随后可同步文档提交，但不因此改变运行代码。
重要限制：本地 `boya_agent.db` 是 0 字节空文件；生产数据库、凭据和上传文件不进入仓库。本轮主 SMTP 曾出现超时但回退/重试完成，当前生产已暂停邮件自动投递，Telegram 仍关闭，长期通知稳定性和真实收件结果仍需持续观察。登录验证、管理员测试邮件和手动推送是独立路径，测试期间不调用。

## 一句话结论

项目是一个单进程轻量服务：抓取北航博雅课程，写入 SQLite，按统一课程状态和用户偏好推送到邮件/Telegram，并提供公开课程页、订阅页、用户门户、二维码共享页和管理员后台。认证、管理员边界、二维码隐私、时间/课程状态、运行权限、网页首屏性能、后台查询性能和“当前无课”空状态已经完成一轮针对性改进；`buaaboya.top` 的 HTTPS、健康检查、管理边界和非 root 服务已在生产主机实测通过。

## 当前能力矩阵

| 能力 | 状态 | 事实位置 |
| --- | --- | --- |
| 课程抓取、解析、去重、稀疏快照保护 | 已实现 | `src/auth.py`、`src/scraper.py`、`src/scheduler.py` |
| SQLite 持久化和增量字段迁移 | 已实现 | `src/models.py` |
| 课程状态、签到标签、热门判断 | 已实现 | `src/course_state.py` |
| 全局和用户级筛选 | 已实现 | `src/filters.py`、`src/push/email_push.py` |
| 邮箱订阅、一次性验证、门户会话 | 已实现 | `web/app.py`、`src/push/email_push.py` |
| 用户门户、提醒、通知时间线、暂停和退订 | 已实现 | `web/app.py`、`web/static/portal.js` |
| RSS/Atom | 已实现，受 `rss_enabled` 控制 | `src/push/rss_feed.py`、`web/app.py` |
| 二维码上传、审核、过期访问控制、贡献榜 | 基础版已实现 | `src/qrcode_service.py`、`web/qrcode_feature.py` |
| 管理后台和管理 API | 已实现，应用层和 Nginx 双层保护 | `web/security.py`、`deploy/nginx_boya.conf` |
| TypeScript 7 前端检查 | 已接入，渐进式检查，当前不改变运行时加载；首页、二维码、持久登录和订阅桥接脚本已纳入 | `package.json`、`tsconfig.json`、`scripts/check-js.mjs` |
| 邮件课程推送 | 已实现，默认关闭 | `FilterConfig.email_enabled` |
| SQLite 通知投递任务 | 部分实现，课程推送已接入 | `src/models.py`、`src/notification_jobs.py` |
| Telegram 课程/摘要/提醒/告警 | 已实现，默认关闭 | `src/push/telegram_bot.py`、`src/scheduler.py` |
| 自动选课 | 实验能力，默认关闭 | `src/enroll.py`、`FilterConfig.auto_enroll_enabled` |
| 跨重启 Playwright 持久会话 | 未实现 | 当前仅复用进程内 browser/context/page |

## 本次重构已完成

- 登录和验证改为短期一次性链接或 6 位验证码；验证码和链接摘要不以明文保存，验证码有过期和尝试次数上限。
- 门户身份只从 `HttpOnly` 会话 Cookie 读取；邮箱不再作为门户身份凭据，旧的长期订阅 token 不能直接登录门户。
- 管理页面和管理 API 增加应用层 Basic/Bearer 校验；状态修改请求增加 Origin/Referer 同源检查；CORS 不再默认全开放。
- 增加安全响应头、反向代理协议识别、生产密钥强制检查和上传请求大小边界。
- 二维码公开列表只显示审核通过且仍有效的记录；公开响应不含邮箱、原始文件名、服务器路径或审核字段；上传做扩展名、实际图片格式、内容、尺寸、大小、哈希和重复校验。
- 统一 `src/course_state.py` 的签到标签、报名窗口、过期和热门判断，并让抓取、筛选、门户、通知、RSS 和二维码使用业务时区。
- RSS/Atom 使用 UTC 发布时间，修复 Atom 中未定义签到标签；RSS 开关现在实际控制公开源。
- 邮件、Telegram、每日摘要、选课提醒和自动选课结果按数据库中的通道开关执行。
- systemd 改用专用低权限用户；Nginx 示例增加 HTTP 到 HTTPS 跳转、TLS server 和遗漏管理接口的鉴权。
- 删除旧的无条件登录邮件实现，前端不再把登录 token 放入 `localStorage`。
- 接入 TypeScript 7.0.2；对首页、二维码、持久登录和订阅桥接脚本启用 `checkJs`，使用 `npm run check` 统一执行 JS 语法和类型检查。
- 增加公开 `/healthz` 探活端点，并让 CI、部署脚本和 Nginx 使用它；详细 `/api/status` 继续保持管理员边界。
- 自动选课增加按业务日累计失败次数的持久化熔断；`confirm_before_enroll` 仍只是提醒，不是阻断式人工审批。
- 门户首屏移除重复课程请求，通知和完整提醒改为按页签懒加载；课程筛选加入请求取消和旧结果保护。
- 提醒序列化改为单次 JOIN，课程/提醒/通知/订阅者高频查询增加幂等组合索引；公开课程、类别、洞察和 RSS/Atom 使用短时缓存。
- 静态资源统一使用带文件版本提示的 URL，并在 Flask/Nginx 层启用长期缓存和文本压缩；历史讨论与邮件预览移入 `docs/archive/`，本地预览改为临时目录输出。
- 将上游选课页面的“暂无课程”识别为合法空快照；门户和管理台区分“当前无课”“筛选无结果”和“加载失败”，并提供刷新与官方选课入口。
- Web 服务改用单进程 Waitress 多线程 WSGI；调度器和 Playwright 继续保持单进程，避免多 worker 重复执行抓取和推送。
- 课程列表生命周期/开选/名额条件下推到 SQLite；抓取落库按批次预取已有课程，推送缓冲、提醒检查和近重复清理减少逐条查询与全表两两比较。
- 课程推送新增 SQLite outbox：按订阅者/课程建立幂等任务，记录处理中租约、指数退避和成功/失败状态；服务重启后由定时恢复任务继续处理。

## 默认关闭或实验能力

- `email_enabled=false`、`telegram_enabled=false`：课程广播默认不发送。
- `daily_summary_enabled=false`：每日摘要默认不发送。
- `auto_enroll_enabled=false`：自动选课必须由管理员显式开启，仍受关键词、每日上限和确认设置约束。
- `rss_enabled=true`：公开 RSS/Atom 默认开启，但可以在管理配置中关闭。
- Playwright 浏览器会话只在当前进程复用，跨重启是否保留 SSO 不作保证。

## 已知问题和待确认项

1. 系统 Python 环境可能缺少 SQLAlchemy 和 Playwright 等项目依赖；项目已统一使用 `uv` 创建 Python 3.12 隔离环境，依赖由 `constraints.txt` 锁定。后续验证以 `.venv` 和 CI 为准，不再使用 Anaconda 全局环境。
2. 生产 HTTPS、Nginx 实际加载结果、systemd 用户权限、公开/管理接口边界和缓存策略已实测。2026-09-04 重启后的真实首轮成功抓取 3 门课程，其中 2 门通过筛选；外部 SSO 登录和邮件 outbox 已实际运行，22 个邮件任务在观察窗口内全部成功，但主 SMTP 多次超时后才由重试/回退完成，不能据此承诺长期稳定。
3. 订阅邮箱和通知事件仍属于业务数据，生产数据库、日志、环境文件和上传目录必须按 [SECURITY.md](../security/SECURITY.md) 保护。
4. 邮件中的退订、暂停和选课提醒仍使用独立的长期操作 token；它们不再是门户登录凭据，但泄露后仍可能触发对应操作，后续可替换为独立的短期操作票据。
5. SQLite 适合单实例轻量运行，不支持无协调的多实例并发写入。
6. `confirm_before_enroll` 当前只发送 Telegram 确认提醒，不会等待用户确认；自动选课虽已有当天失败熔断，但仍应视为高风险实验能力。
7. outbox 已覆盖课程邮件、课程 Telegram、选课提醒（邮件/Telegram 各自独立任务）和 Telegram 每日汇总；站点调整通知和自动选课结果仍保留旧的直投路径，列为下一批高风险改造，需先定义全局任务幂等键、订阅者快照和结果事件语义。
8. outbox 对外部通道提供“至少一次”投递语义；若进程在外部服务已接受消息后、任务状态落库前崩溃，仍存在极窄的重复投递窗口，不能宣称绝对 exactly-once。

## Breaking changes

- `POST /api/login/request` 不再仅凭已注册邮箱建立门户会话；它只发送一次性登录链接和验证码。
- 登录邮件链接先显示确认页，用户点击“确认登录并进入门户”后才建立会话；这是为避免邮件安全扫描器提前消费链接。
- `/portal?email=...`、`/portal?token=...` 不再提供身份登录；门户必须先完成邮箱验证或一次性登录。
- `localStorage` 不再保存门户登录 token。浏览器升级后，用户可能需要重新获取一次邮件。
- 二维码 pending/rejected/过期记录不再能从公开列表或文件地址读取。

## 下一阶段

下一阶段按 [IMPROVEMENT_BACKLOG.md](IMPROVEMENT_BACKLOG.md) 和 [RELEASE_CANDIDATE.md](RELEASE_CANDIDATE.md) 执行，重点是生产观察、真实课程周期验证和通知路径的分批改进。

1. 固定本地和 CI 的完整测试环境，真实记录未运行的检查。
2. 建立模拟课程、无课、登录失效、解析失败、通知失败和重复投递样例。
3. 扩展通知 outbox 到提醒、每日汇总和管理端通知，再逐步拆分 Playwright、调度器、后端路由和门户前端。
4. 每个阶段通过测试后再部署；代码样例默认关闭邮件、Telegram 和自动选课，当前生产 `FilterConfig` 为邮件和每日摘要关闭、Telegram 与自动选课关闭，后续变更前必须核对现场开关。

本阶段改进和部署汇报按照 [REPORTING_STANDARD.md](REPORTING_STANDARD.md) 执行，用户可见文字按照根目录的 [PLAIN_LANGUAGE_REVIEW_PROMPT.md](../../PLAIN_LANGUAGE_REVIEW_PROMPT.md) 审阅。

## 当前生产版本（2026-09-07）

- `main` 和 `codex/ts7-and-hardening` 的应用代码均为 `92e53b2c7645bc521dab7e2891b41e7d6cc9e86d`；服务器运行该应用代码，服务器仓库可继续快进同步文档提交，工作树保持 clean。
- 抓取器将课程行或明确空状态作为主要等待条件，减少固定时长等待；正常轮次默认不保存诊断截图，并记录最近一次抓取耗时。
- 门户请求增加超时、取消和重复初始化合并；课程列表、刷新按钮和页签增加状态反馈、可访问性语义和窄屏样式支持。
- 根目录旧审计快照已移入 `docs/archive/legacy/`；新增 `scripts/verify_release.py` 作为统一发布候选版本验证入口。
- TypeScript 7 检查范围扩展到首页、二维码、持久登录和订阅桥接脚本；门户和管理台脚本暂不一次性改写。
- 生产工作流 `34050088901` 已完成；部署前生成 SQLite 备份，服务启动、首次抓取、公开页面/API、数据库完整性和权限边界均已核对。

### 2026-09-07 生产发布验收

- 服务器运行 `main@92e53b2c7645bc521dab7e2891b41e7d6cc9e86d`，`boya-agent` active，工作树 clean。
- 部署前备份为 `/var/lib/boya-agent/backups/boya_agent-20260907-015623-pre-92e53b2.db`；SQLite `integrity_check` 返回 `ok`，`notification_jobs` 表存在。
- 本机和 `https://buaaboya.top/healthz` 返回 `200`；主页、订阅页、门户、`/api/courses`、`/rss` 返回 `200`；HTTP 正确跳转 HTTPS；未授权 `/api/status` 返回 `401`。
- 静态资源缓存为 `public, max-age=604800, immutable`；邮件开关恢复为 `1`，Telegram 和自动选课保持 `0`。
- 首次抓取已完成并报告当前没有可选课程；真实课程尚未开展，后续按课程周期验证实际发现、筛选和提醒效果。

## 本批自动化验证（2026-09-07）

- `uv run --python .venv scripts/verify_release.py`：当前隔离环境验证通过；Python 编译检查、JavaScript 语法检查、TypeScript 7 检查和差异检查均通过。
- `uv run --python .venv scripts/verify_release.py --require-clean`：作为收尾门槛执行；要求工作树无未提交或未跟踪文件。
- 生产发布期间邮件投递暂时抑制，完成后恢复原开关；邮件、Telegram 和自动选课的业务配置未修改。

## 当前测试期生产配置（2026-09-08）

- `email_enabled=0`、`daily_summary_enabled=0`、`telegram_enabled=0`、`auto_enroll_enabled=0`。
- 已生成备份 `/var/lib/boya-agent/backups/boya_agent-email-paused-20260908-024504.db`，修改后 SQLite 完整性未受影响；服务未重启，`boya-agent` 仍为 active。
- 6079 条历史邮件通知任务均为 `succeeded`，没有待处理邮件任务；未发送选课提醒数量为 0。
- 当前开关可以阻止调度器的自动邮件路径，但不能替代底层 SMTP 总开关；邮箱验证/登录、管理员测试邮件、手动推送和站点通知在代码层仍需避免调用，最终版前要补上统一保护。

## 历史实施记录（2026-09-04）

- 抓取器新增结构化结果契约，区分正常有课、正常无课、登录失效、上游不可用、解析失败和超时。
- 调度器已消费结构化结果；失败结果不会进入课程落库流程，并会记录 `last_scrape_status`。
- 保留旧的 `scrape_courses()` 列表接口，便于后续按批次迁移调用方。
- 课程邮件和课程 Telegram 推送已接入 SQLite outbox，支持幂等创建、处理租约、指数退避和定时恢复；提醒及每日汇总路径暂未迁移。
- 本地已完成 65 项测试、1 项跳过、Python 编译检查、前端 `npm run check` 和差异检查；上述记录对应 2026-09-04 的已部署版本，不包含 2026-09-07 发布记录。

## 上一轮生产部署观察

- Git（本轮部署，2026-09-04）：本地、GitHub `codex/ts7-and-hardening` 和服务器当前分支已核对无差异，本地分支 ahead/behind 为 `0/0`，服务器工作树 clean；应用提交为 `2eb1e2a`，服务器 deploy key 仅保留拉取权限，生产推送使用已授权的 HTTPS Git 凭据完成，详见 [RELEASE_NOTES.md](RELEASE_NOTES.md)。
- 生产主进程于 `21:32:46 CST` 重启后保持 active；本机和 `https://buaaboya.top/healthz` 返回 200；主页、订阅页、门户、课程/类别接口和 RSS 返回 200；未授权 `/api/status` 返回 401；HTTP 正确跳转 HTTPS。
- 生产数据库中的 `notification_jobs` 表、唯一幂等键和状态/渠道索引存在，SQLite `integrity_check` 为 `ok`；部署前已生成一致性备份，邮件任务观察窗口内为 `22 succeeded`、无未解决任务。

## 本轮实际验证

- `python -m compileall -q src web tests`：通过。
- `web/static/` 下 6 个 JavaScript 文件逐一执行 `node --check`：通过。
- `uv run --python .venv python -m pytest -q tests/test_qrcode_feature.py tests/test_course_state.py tests/test_rss_feed.py tests/test_enroll_safety.py`：历史专项验证通过。
- `uv run --python .venv python -m pytest -q tests/test_scraper_scheduler_regressions.py`：历史专项验证通过。
- `D:\\Anaconda\\python.exe -m pytest -q tests/test_web_security.py`：已纳入全量测试并通过。
- `npm run check`：6 个 JavaScript 文件语法检查通过，TypeScript 7 类型检查通过。
- `python -m pytest -q`：未通过收集，4 个测试模块因默认环境缺少 `sqlalchemy` 或 `playwright` 报错；未将环境阻塞伪装成测试通过。
- `uv run --python .venv python -m pytest -q`：当前推荐入口；依赖由 `constraints.txt` 管理。
- 服务器临时测试环境：`50 passed`；未向生产运行虚拟环境安装 pytest 或开发依赖，Waitress 作为核心生产依赖已安装。
- 线上 `https://buaaboya.top`：主页、门户、订阅页和公开接口返回正常；`/api/courses`、`/api/categories`、`/rss` 缓存策略生效，静态资源 URL 带版本参数并返回 `public, max-age=604800, immutable`；HTTP 正确跳转 HTTPS，未授权 `/api/status` 返回 401。
- 生产无课场景：定时抓取日志已记录“选课页面已加载，当前暂无可选课程”和“按空状态完成本轮抓取”，没有再记录“无法进入选择课程页面”。
- 门户/管理台空状态资源：线上 `portal.js` 已包含“当前暂无可选课程 / 没有匹配的课程”分支，`portal.css` 和管理台样式已加载对应空状态布局。
- 生产数据库：新增课程、提醒、通知和订阅者组合索引已存在；systemd 服务以 `boya-agent` 用户运行，服务和 Nginx 均 active，部署后健康检查返回 `{"status":"ok","success":true}`。
- 性能基准：线上 5 个公开接口各连续请求 20 次均为 200；首页、健康、课程、洞察、类别接口中位数约 `11.1–12.5 ms`，P95 约 `12.5–14.3 ms`；24 个并发课程请求全部返回 200，整体约 `883 ms`。
- Git（历史部署记录，2026-09-03）：`codex/ts7-and-hardening` 的 `1fbadd6` 已部署至服务器；服务器工作树 clean。
- `git diff --check`：通过；仅有 Git 关于 LF/CRLF 的换行提示。
- 本轮新增抓取、调度和通知 outbox 回归覆盖；当前完整验证统一通过 `uv run --python .venv scripts/verify_release.py` 执行。
