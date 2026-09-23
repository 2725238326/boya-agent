# 本地预发布环境

文档状态：当前本地预发布边界；更新时间：2026-09-24。

## 目的

本地预发布用于验证配置、数据库迁移、固定课程样例、通知 outbox、页面行为和失败恢复。它不执行真实北航课程抓取，也不使用腾讯云生产账号、生产数据库或生产上传文件。

当前本地预发布工作树：

```text
E:\Demo\boya-agent-staging
```

该工作树固定在独立 Git checkout 中，使用自己的 `.venv`、日志目录、上传目录和运行数据库。

## 环境边界

| 项目 | 本地预发布 | 腾讯云生产 |
| --- | --- | --- |
| 北航账号 | 不配置 | 使用服务器现有凭据 |
| 数据库 | `runtime/boya_agent.staging.db` | `/var/lib/boya-agent/data/boya_agent.db` |
| 端口 | `127.0.0.1:5001` | Nginx → `127.0.0.1:5000` |
| 抓取数据 | 固定样例，待接入 fixture provider | 真实课程系统 |
| 邮件 | 模板预览和 dry-run，待接入文件适配器 | 当前关闭 |
| Telegram | 关闭 | 当前关闭 |
| 飞书 | 未配置、暂缓 | 未配置 |

## 创建环境

```powershell
git worktree add --detach ..\boya-agent-staging HEAD
Set-Location ..\boya-agent-staging

uv venv --python 3.12 .venv
uv pip install `
  --python .venv\Scripts\python.exe `
  -r requirements-dev.txt `
  -c constraints.txt

.venv\Scripts\python.exe -m playwright install chromium
```

## 运行约束

当前代码仍然需要增加显式的 fixture/dry-run 运行模式后，才能启动完整的本地预发布调度。实现该模式前，不要在本地填写生产北航账号，不要执行会访问北航系统的启动命令。

预发布目标配置规划为：

```dotenv
APP_ENV=staging
WEB_HOST=127.0.0.1
WEB_PORT=5001
DATABASE_PATH=E:/Demo/boya-agent-staging/runtime/boya_agent.staging.db
NOTIFICATION_MODE=dry_run
EMAIL_DELIVERY_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

`APP_ENV`、`NOTIFICATION_MODE` 和 fixture 抓取开关在代码正式实现前属于规划配置，不得直接复制到生产环境。

## 验收目标

1. 不访问真实北航系统。
2. 不连接真实 SMTP、Telegram 或飞书。
3. 使用固定课程样例覆盖有课、无课、已满、已结束、异常和恢复。
4. 通知任务可以创建、去重、重试和恢复。
5. 邮件 dry-run 生成可审阅的本地文件。
6. 预发布数据库、日志和上传目录与其他环境完全分离。
