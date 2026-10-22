# 本地预发布环境

更新时间：2026-10-22。状态：fixture、文件邮件和隔离启动已实现并通过本地及云端演练；代码随 73e04b1 部署，生产仍使用真实抓取，邮件继续关闭。

本地预发布用于验证数据库升级、课程样例、邮箱验证和登录、通知 outbox、页面及故障恢复。入口在导入应用之前设置独立数据库、日志、上传目录和随机开发密钥，跳过 .env，清除继承的校园、SMTP 和 Telegram 配置。

## 启动

在拥有项目依赖的 checkout 根目录执行，支持现有 `E:\Demo\boya-agent-staging`；该旧 checkout 必须先包含本次代码，不能仅复制配置变量。

```powershell
.venv\Scripts\python.exe scripts/run_staging.py
```

浏览器访问 `http://127.0.0.1:5001`。默认文件位置：

| 内容 | 位置 |
| --- | --- |
| 数据库 | runtime/staging/data/staging.db |
| 日志 | runtime/staging/logs/ |
| 二维码 | runtime/staging/uploads/ |
| 邮件预览 | runtime/staging/mail/*.eml |

打开 .eml 可查看实际验证码和链接，完成验证及门户操作。邮件只写本地文件，不连接 SMTP。预览含测试身份票据，放在受控目录，不提交、不公开。runtime/ 已被 Git 忽略；Linux 文件权限为 0600，Windows 采用目录继承 ACL。

可使用独立运行目录、端口和故障场景：

```powershell
.venv\Scripts\python.exe scripts/run_staging.py --root E:/Demo/boya-agent-staging/runtime/demo --port 5002 --scenario healthy
.venv\Scripts\python.exe scripts/run_staging.py --once --scenario timeout
```

场景：healthy（可报名、已满、未开选、已结束）、empty、auth_expired、upstream_unavailable、parse_failed、timeout。时间相对业务时钟生成，课程 ID 固定。已结束样例不入有效课程库；故障场景不会覆盖可靠数据。更换场景时重新启动，已有数据库保留；测试全新空库应指定新的 root。

`--once` 成功退出 0，抓取失败退出 1。启动器随机生成开发管理员密码，不复用生产身份；需要管理员验收时，可在受控 Python 启动脚本中调用 configure_staging 后设置本地管理员密码再启动 main，不把生产凭据带入。

## 可重复验收

```powershell
.venv\Scripts\python.exe scripts/verify_staging.py
```

该命令创建临时运行目录，并阻断真实 socket connect；验证空库和重复初始化、六类抓取结果、故障保留数据与恢复、注册、邮件预览、一次性验证码、门户会话、登录邮件、权限边界、任务去重、退避重试和过期租约恢复。通过后删除临时测试产物。数据库旧表升级另由 test_database_upgrade.py 覆盖。统一 verify_release.py 已包含本演练。

## 运行边界

APP_ENV=staging 必须配套 SCRAPE_SOURCE=fixture 和 NOTIFICATION_MODE=dry_run；数据库、日志、上传和预览路径全部位于绝对 STAGING_ROOT 内，监听 127.0.0.1 且端口不小于 5001。配置不满足时拒绝启动。真实浏览器与 Telegram 创建入口在 staging 拒绝调用，自动选课调用跳过。

这些变量已实现，但应使用启动器成套配置，避免把路径、旧密钥或外部凭据混入。生产默认使用真实抓取及 smtp 模式，生产不接受 dry_run。固定样例与文件发送成功不代表真实校园登录、SMTP 送达或生产长期稳定性已验证。
