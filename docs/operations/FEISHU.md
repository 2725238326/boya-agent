# 飞书课程群推送

更新时间：2026-10-24。需求见 [找课与通知](../product/NOTIFICATION_REQUIREMENTS.md)，本轮上线状态见 [采集与卡片验收](../project/SCRAPE_CARDS_RELEASE_2026_10_24.md)。早期接入过程与混合告警测试是 [历史验收](../project/FEISHU_RELEASE_2026_10_24.md)，后续用户明确要求运维改用 Telegram。

## 群用途与消息

飞书服务同学发现符合全局筛选的课程及退选补位，使用签名自定义机器人，无需新增应用权限。业务消息采用 interactive 卡片：普通课程蓝色、补位橙色，标题为课程名及通知原因。正文突出名额、截止/开选时间、签到、上课时间、地点及已知开放对象；采集时间明确标示。主按钮进入官方选课，次按钮定位本系统该课并展开详情。

外部课程字段按纯文本呈现；加粗名额仅由整数生成，不把外部文字直接作为 Markdown。未知字段显示待确认。消息是发出时快照，名额和报名资格以官方系统为准，点击不会自动报名。个人邮箱、提醒、登录票据和运维错误不发到同学群。

## 配置与启用

| 配置 | 默认 | 作用 |
| --- | --- | --- |
| FEISHU_WEBHOOK_URL | 空 | 群机器人固定 HTTPS 地址，凭据 |
| FEISHU_WEBHOOK_SECRET | 空 | 必需的签名密钥，凭据 |
| FEISHU_COURSES_ENABLED | false | 独立课程用途开关 |
| FEISHU_ALERTS_ENABLED | false | 保留兼容配置；学生群不处理系统告警，生产工作流要求 false |

不依赖邮件或课程 Telegram 开关；每日摘要继续受原全局每日任务开关控制，不因飞书启用而打开。管理员 `/api/status` 的 feishu 提供 configured/courses_enabled/role，alerts_enabled 固定为 false，不输出凭据。

飞书侧：对话模式群 → 群机器人 → 自定义机器人，启用签名校验；如使用关键词，所有模板包含“博雅”。IP 限制应包含实际服务器出口及本机验收出口。支持国内 open.feishu.cn，拒绝重定向，不使用任意代理。

凭据保存在排除 Git 的 key/webhook.txt：第一行 Webhook，第二行签名密钥，也支持对应环境变量格式；不要粘贴到聊天或命令参数。

```powershell
# 解析及导入 GitHub production Secrets；均不输出值
.venv\Scripts\python.exe scripts/feishu_config.py --config key/webhook.txt
.venv\Scripts\python.exe scripts/feishu_config.py --config key/webhook.txt --upload-github-secrets

# 本地结构预览与三张明确标注的课程卡片演练
.venv\Scripts\python.exe scripts/preview_feishu_cards.py
.venv\Scripts\python.exe scripts/feishu_config.py --config key/webhook.txt --send-card-test

# 运行代码和配置脚本与工作流版本内容一致、工作树 clean 时启用飞书课程
# 当前 Telegram 按用户决定保持关闭，待网络打通再启用
gh workflow run configure-feishu.yml --ref main -f alerts_enabled=false -f courses_enabled=true -f ops_enabled=false
```

接口 code=0 表示飞书接受，不替代群内人工确认。本地结构预览使用相同 JSON，不能冒充客户端截图。配置工作流核对 src/web/config/deploy、生产依赖及配置脚本的内容一致和工作树 clean；只改文档/控制工作流时，不必为了 SHA 不同重启同一份应用。先用相同参数加 `-f check_only=true` 可只读比较有效进程配置。

## 触发、去重和时效

课程沿用后台全局筛选，入队与投递前均校验。新课按预告/正式开选阶段分别幂等；旧版成功记录参与迁移去重。退选补位按一次真实重新放额观测去重，同为“剩余 1 个”在后续满员再开放时仍可通知。普通刷新与人数变化不重复发送首次课程卡片。

超过 10 分钟未被采集确认的课程不新建群任务，已有过期快照在发送前跳过。个人页面对陈旧在报名课程提示“名额待核对”，并显示本课采集时间。全局最近采集成功不等于每门课程都被重新命中。

每门课一个任务，跨即时/缓冲/active_watch 去重；每日摘要按日期和课程区分，前一日未投递摘要跳过。旧群任务根据不可逆目标指纹跳过，避免历史任务投到新群。历史系统告警任务保留作审计，不领取、不投到学生群。

抓取仅入队，不等待 HTTPS；后台默认约 30 秒扫描，每次串行间隔至少 1.1 秒，HTTP 单步超时 10 秒，正文上限 19000 字节、响应 64 KiB。仅 HTTP 200 且整数 code=0 算接受。失败有界退避，终态任务不因普通巡检无限重置。过期/筛选/陈旧/旧群任务可成功跳过但 delivered_count=0，真实课程成功另记 PushLog，不能只凭 succeeded 总数推断送达。响应不明确时不立即换文本重发；接受与落库之间仍有至少一次投递窗口。

## 关闭与回退

```powershell
gh workflow run configure-feishu.yml --ref main -f alerts_enabled=false -f courses_enabled=false -f ops_enabled=false
```

可独立控制 Telegram 运维，当前按用户决定关闭。工作流私有备份环境、保留其他值；先私下比较进程中的课程开关、凭据及运维状态，不输出值。若有效配置一致且运维关闭，只同步无运行影响的旧配置，不发重复探测、不重启；实质变化仍探测、串行重启并验证真实采集和实际进程。失败恢复旧环境，只有本次实际重启过才重启回退。备份含凭据，权限 0600，只记录路径，不上传 Git 或公开工单。Telegram 检测细节见 [运维手册](TELEGRAM_OPS.md)。

staging 清除并拒绝 FEISHU_* 与 TELEGRAM_OPS_* 配置，传输入口也阻断 staging；离线回归不连接真实群。
