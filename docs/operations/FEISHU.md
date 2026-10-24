# 飞书群推送配置与运维

更新时间：2026-10-24。代码已实现，本次发布和真实运行结果见 [飞书接入记录](../project/FEISHU_RELEASE_2026_10_24.md)。

## 能力和开关

使用群自定义机器人，签名消息只发送到该 Webhook 绑定的群。支持课程提醒、报名动态、退课补位、每日汇总以及连续抓取失败/恢复通知。课程沿用数据库全局筛选；不发送个人邮箱、个人提醒或登录票据，不提供群内命令/按钮回调或个人私聊。

| 环境变量 | 默认 | 作用 |
| --- | --- | --- |
| FEISHU_WEBHOOK_URL | 空 | 群机器人 HTTPS Webhook，属于凭据 |
| FEISHU_WEBHOOK_SECRET | 空 | 签名校验密钥，必须配置 |
| FEISHU_ALERTS_ENABLED | false | 连续失败告警及恢复通知 |
| FEISHU_COURSES_ENABLED | false | 课程和每日汇总的群消息 |

只有显式 true 开启对应用途。两项互相独立，不依赖 email_enabled 或 telegram_enabled。每日汇总仍受原 daily_summary_enabled 和执行时间控制；启用飞书不会开启每日摘要或邮件。配置修改重启生效。配置缺失/错误时飞书不启用，管理员 /api/status 的 feishu 字段仅显示是否配置及用途开关，不输出凭据。

课程发送前重新校验最新筛选及有效期。每门课程单独一个任务，避免批次部分失败导致全部重发；同群同新课程跨 priority/active_watch/缓冲模式只创建一份任务。每日汇总按日期、每门课程分别幂等，前一天未投递的汇总不会补发。更换群 Webhook 后旧群任务被跳过，避免历史任务投到新群。

故障告警采用现有连续失败阈值 3，冷却期沿用 SCRAPE_ALERT_COOLDOWN_MINUTES（默认 60）。告警和恢复均先持久化；冷却及故障编号从任务表恢复，重启后不靠内存判断。恢复记录创建后，未发送的故障告警跳过；超过 2 小时的告警跳过。任务中的 system:scrape 是系统事件标记，不是真实课程。

## 飞书侧设置

1. 在对话模式测试群中打开群设置 → 群机器人 → 添加自定义机器人。
2. 开启签名校验，分别保存 Webhook 和签名密钥。初次不叠加关键词/IP 限制，连通后再按需要配置。
3. 如果使用关键词，“博雅”包含在所有模板中。IP 白名单应允许生产服务器实际出口 IP；本机验收也需允许本机出口。
4. Webhook 使用 open.feishu.cn 国内版地址；当前不接海外 Lark 域名、带认证代理或任意重定向。

## 本机验收与凭据保存

可在排除 Git 的 key/webhook.txt 保存两行，第一行为 Webhook，第二行为签名密钥；也接受对应 FEISHU_WEBHOOK_URL/FEISHU_WEBHOOK_SECRET 键值格式。不要把真实内容粘贴进文档、终端命令或提交。

```powershell
# 只读解析，不发送消息，不输出值
.venv\Scripts\python.exe scripts/feishu_config.py --config key/webhook.txt

# 向文件所指群发送 3 条明确标注的告警/恢复/课程样例
.venv\Scripts\python.exe scripts/feishu_config.py --config key/webhook.txt --send-test

# 保存到 GitHub production 环境的两个 Secret，不在命令参数中传值
.venv\Scripts\python.exe scripts/feishu_config.py --config key/webhook.txt --upload-github-secrets
```

返回成功表示飞书接口 code=0，群内实际可见还需人工确认。该工具不会注册北航/邮箱用户或写业务数据库；测试课程有明确标注，不代表真实课程。

## 生产激活

先将通过 CI 的代码部署并确认服务器运行工作流目标提交；生产环境需拥有两个同名 GitHub Secrets。然后运行独立配置工作流：

```powershell
# 只开放告警
gh workflow run configure-feishu.yml --ref main -f alerts_enabled=true -f courses_enabled=false

# 在已授权测试群内同时开放课程和告警
gh workflow run configure-feishu.yml --ref main -f alerts_enabled=true -f courses_enabled=true
```

工作流与常规 main 部署串行，检查服务器提交/clean；先发送服务器验收消息，再备份 .env 并只修改四个 FEISHU_* 值。保留其余配置、原属主和权限，原子替换环境文件，重启并检查真实抓取成功及实际进程中的飞书开关。失败恢复原环境文件并尝试恢复服务。备份含凭据，权限 0600，仅输出路径；不要上传备份到 Git 或公开工单。

启用课程可能立即对当前可报名且符合筛选的课程建立任务；这是已有 active_watch 规则。并非只通知启用后的新入库课程。旧 email/Telegram 任务不受影响。个人提醒不转发到群。

## 队列、重试和回退

飞书复用 SQLite notification_jobs；创建与投递分离，抓取不等待 HTTPS。独立扫描默认约 30 秒；单进程串行间隔至少 1.1 秒，单次 HTTP 操作超时 10 秒，正文上限 19000 字节，响应上限 64 KiB。不依赖系统代理、不追随重定向；只接受 HTTP 200 且 JSON 整数 code=0。

临时网络错误、飞书限频和拒绝进入现有最多 3 次尝试、30/60 秒指数退避。终态失败不因巡检重复出现无限重置。关闭的用途不领取任务、不消耗重试。已失效/被筛选/旧群/已恢复的任务可能标记成功跳过，delivered_count=0；真实群发送成功另写 PushLog，不能只凭 succeeded 总数推断送达。

所有外部通知仍为至少一次语义，进程在外部接受消息后、数据库落库前崩溃仍有重复窗口；不承诺绝对不重复。

管理员 /api/status 可查看 feishu 及各渠道任务数量；只读诊断工作流只显示配置是否存在、开关和状态计数，不输出 Webhook、secret 或 payload。日志只显示异常类型/HTTP 状态/数字业务码，不保存第三方异常原文。

关闭飞书：

```powershell
gh workflow run configure-feishu.yml --ref main -f alerts_enabled=false -f courses_enabled=false
```

关闭操作不发送探测消息，保留凭据和任务，后续恢复时重新检查有效期。若 GitHub main 已有纯文档后续提交而服务器版本不同，先按受控发布同步代码再操作；不要绕过精确提交检查。

## 验证范围

自动化检查覆盖 HMAC、TLS Webhook 路径约束、响应业务码、限频、重定向拒绝、超长内容、后台线程、独立开关、持久队列去重/重试、变更筛选/过期/群切换、告警冷却与跨重启恢复、配置备份及非飞书值保留。

staging 显式清除并拒绝 FEISHU_* 配置，飞书传输入口亦阻断 staging；飞书验证使用 mock 传输，真实群验收单独运行。不在课程抓取 fixture 演练期间真实发群消息。
