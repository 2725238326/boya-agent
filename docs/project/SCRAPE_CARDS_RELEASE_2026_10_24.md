# 2026-10-24 采集准确性、学生卡片与 Telegram 运维

状态：已部署，飞书学生课程已启用，23:10 独立生产诊断通过。应用运行 `04fc2086c487b27a5aff71dd1fe05cfe9541248e`；控制工作流为 `c683d8846c43d7a5fddcbfd76c7bfad426851f4e`，其运行代码与服务器内容一致。后续文档提交不代表应用重新部署。时间均为 Asia/Shanghai，备份文件名保留原值。

用户确认飞书服务同学找课、报名和退选补位，Telegram 服务运维；服务器 Telegram 网络预检失败后，用户明确选择暂不配置代理，待网络打通再启用。需求见 [找课与通知](../product/NOTIFICATION_REQUIREMENTS.md)。

## 实际交付

- 飞书只领取学生课程任务，排除历史运维任务；配置工具禁止开启飞书告警，实际进程 role=student_courses、courses_enabled=true、alerts_enabled=false。
- 卡片标题包含课程名和原因，名额、截止/开选时间前置，显示签到、上课与地点、已知开放对象、本课采集时间；主按钮进入官方选课，次按钮定位并展开对应课程。外部字段按纯文本呈现，名额加粗只使用整数。
- 预告/正式开选分阶段去重，补位按实际重新放额观测去重，旧版成功记录参与迁移。超过 10 分钟的陈旧快照不新建群任务，投递前再次检查；首页提示名额待核对，不以全站成功时间冒充每门课程刚被确认。
- Telegram 运维独立开关、outbox、持久冷却/恢复与过期控制；独立 systemd 定时器代码和 unit 已准备。当前 enabled=false，timer 未启用，真实 Telegram 投递和现场运维验收尚未完成。
- 邮件、课程 Telegram、每日摘要和自动选课仍关闭，RSS 开启。自动选课保持冻结。

## 采集准确性证据

23:09:33 的真实日志确认 `pages=7, unique=131, declared_total=unknown`。原始记录包含已结束课程，不能等同可报名数。数据库共 94 条历史记录，当前有效 14 门，其中 13 门近期重新命中，1 条历史记录较久未命中，显示名额待核对并排除新群推。

完整表格摘要覆盖所有可见课程表，不再只比较前 240 字符；人数/容量采用后续完整观测对，空详情保留已有字段。签到分类只使用签到字段，缺失显示待确认，自主选课不再被误当自主签到。

分页兼容 disabled/祖先标记、数字页码和真实校园首页/末页控件；限定导航范围，按原生 DOM 事件操作只读分页，应对遮挡和布局变化。延迟更新有限等待；重复页、中途空页、会话丢失、超限和声明总数不符拒绝不完整轮次。旧分页没有可读总数时使用独立首页/末页复核；该方式依赖控件按标示工作，仍不是独立总数证明，日志明确保留 unknown。

最终 14 门有效记录的开始/报名时间、容量、签到均无缺口；公开 API 结束时间缺失为 0。无负人数、时间倒置和完全重复身份；超过 10 分钟未命中的记录为 1。历史数据保留作核对，陈旧名额不作为新的报名机会推送。

## 验证与发布

| 环节 | 结果与证据 |
| --- | --- |
| 完整检查 | 最新云端 Python 3.10/3.12 各 162 项测试、53 项子测试通过；依赖、编译、隔离预发布/邮件恢复、JS/TypeScript、全部浏览器及真实独立 systemd 恢复通过 |
| PR #5 | [学生卡片与运维分工](https://github.com/2725238326/boya-agent/pull/5)，合并 20c3cf1；[部署 40102118346](https://github.com/2725238326/boya-agent/actions/runs/40102118346) 成功，22:27:23 启动采集验收通过 |
| PR #6 | [分页遮挡与配置预检](https://github.com/2725238326/boya-agent/pull/6)，合并 04fc208；[部署 40103437024](https://github.com/2725238326/boya-agent/actions/runs/40103437024) 成功，22:43:44 启动采集验收通过 |
| PR #7 | [等效配置保留进程](https://github.com/2725238326/boya-agent/pull/7)，控制工作流 c683d88；[PR CI 40105104612](https://github.com/2725238326/boya-agent/actions/runs/40105104612) 双 Python 全过；不触发应用部署 |
| 只读配置比较 | [40105414869](https://github.com/2725238326/boya-agent/actions/runs/40105414869)，restart_needed=false，未修改配置或重启 |
| 最终配置同步 | [40105639159](https://github.com/2725238326/boya-agent/actions/runs/40105639159) 成功，23:09:05 验证实际进程；仅同步环境文件，保留原进程 |
| 最终独立核对 | [Server Diagnostics 40105752033](https://github.com/2725238326/boya-agent/actions/runs/40105752033) 成功，23:10:35 核对版本、队列、数据库、资源和用途开关 |

实际 Chromium 离线回归覆盖长公共开头/尾部差异、延迟分页、祖先禁用、返回第一页、数字边界、真实无标记首页/末页、遮挡和下一页失效但末页移动的故障注入。首页覆盖课程定位、展开、清除定位、满员/陈旧名额、双宽度和无横向溢出；生产 390px 实际课程链接、展开与清除也已通过。

飞书新版学生卡片的补位、新课与预告三张明确标注演练的样例均获接口 code=0；本地结构预览使用相同 JSON，不是飞书客户端截图。生产累计课程成功 PushLog 为 6 条，最近为 22:32:40，发生在卡片代码部署后；队列共 8 个 succeeded，其中新课 3、补位 3、历史告警/恢复各 1。后两条属于首次混合接入的历史验收，当前不再派发。无 pending/processing/failed。接口接受不替代群内人工可见确认。

## 配置过程与 GitHub 邮件

Server Diagnostics 40099944774 的核心生产检查通过，额外校园浏览器审计发生 TimeoutError，导致整条工作流失败并触发 GitHub 邮件；它没有部署或修改业务数据。临时上游审计现已拆到手动 Upstream Pagination Audit，不再混入日常生产诊断，也不会吞掉审计失败伪报成功。

Telegram 预检任务 40102743416 在环境修改前遇到网络 URLError，未激活运维。随后课程配置任务 40103824390 遇到冷启动采集超出五分钟窗口，恢复旧环境并重启；正常采集在边界附近已返回，但回退打断了会话。两次配置流程问题已修正：预检失败不重启；有效配置和凭据一致且运维关闭时只同步文件，既不重发探测，也不重启。实质变化仍保留探测、重启、启动后采集检查及回退。

当前配置工作流要求工作树 clean，src/web/config/deploy、生产依赖和配置脚本与已审阅版本内容一致，允许文档/控制工作流后续提交。比较助手从已审阅 Git 对象读取，仅私下比较进程配置，输出布尔值，不输出凭据。最终应用 PID=3644618，启动时间仍为 22:49:24，证明 23:09 的同步保留了进程。

## 23:10 运行快照

- 工作树 clean，服务 active/enabled，1 个 Playwright driver；SQLite integrity_check=ok，最近 last_seen=23:09:34.600527。
- 实际进程：飞书 configured=true、courses_enabled=true、alerts_enabled=false、role=student_courses；Telegram 运维 configured=true、enabled=false、role=operations。配置存在不代表 Telegram 已可达。
- 数据库业务配置 `(interval_minutes, email, telegram, daily_summary, auto_enroll, rss)=(10,0,0,0,0,1)`。历史 email succeeded=6079，本轮没有开启邮件发送。
- 公网 healthz、首页、订阅页、门户和二维码页均 200，未授权管理状态 401；课程 API 14 门，23:09:34 最近成功采集，degraded=false。订阅/登录按钮保持停用。
- cgroup 内存 397783040 bytes，限制 1300 MiB；主机 available 836 MiB，Swap 约 1.6/2 GiB，根盘 71%，剩余约 12 GiB。

## 备份和回退

- 20c3cf1 发布前数据库：`/var/lib/boya-agent/backups/boya_agent-20261024-222348-pre-20c3cf1.db`；二维码：`qrcode-20261024-222349-pre-20c3cf1.tar.gz`，同目录；恢复目录 `/var/lib/boya-agent/release-recovery.0SN5QU`。
- 04fc208 发布前数据库：`/var/lib/boya-agent/backups/boya_agent-20261024-224121-pre-04fc208.db`；二维码：`qrcode-20261024-224121-pre-04fc208.tar.gz`，同目录；恢复目录 `/var/lib/boya-agent/release-recovery.bQq9zi`，上一应用版本 20c3cf1。
- 最终配置私有备份：`/var/lib/boya-agent/backups/feishu-env-pre-20261024-150904-684397.bak`，0600，含凭据，不能上传 Git/公开工单。

操作见 [飞书手册](../operations/FEISHU.md)、[Telegram 运维](../operations/TELEGRAM_OPS.md)；决定见 [ADR 0007](../decisions/0007-student-cards-and-telegram-operations.md)。

## 明确剩余

Telegram 网络打通、真实接收和独立 timer 现场验收按用户决定暂缓；当前模块和 unit 已准备，未启用。邮件仍需指定测试收件箱的真实 SMTP、验证/登录、域名发信认证与分阶段开放验收。继续观察校园 SSO 波动、分页覆盖、陈旧记录、Swap 和磁盘。卡片不是实时名额面板，外部投递仍有至少一次窗口；没有独立上游总数时不承诺绝对全量或长期零故障。
