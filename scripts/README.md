# 脚本入口与产物

更新时间：2026-10-24。在仓库根目录运行；生产脚本按对应工作流和 [运维手册](../docs/operations/RUNBOOK.md) 使用。

| 用途 | 入口 | 状态或产物 |
| --- | --- | --- |
| 统一发布验证 | `verify_release.py` | 依赖、编译、仓库规范、pytest、隔离演练、前端与差异检查 |
| 文档/架构/目录规范 | `check_repository.py` | 当前本地链接、依赖方向、Git 已跟踪运行文件；只读 |
| 依赖锁定 | `freeze_constraints.py` | `constraints.txt`；`--check` 只比较 |
| 前端检查 | `check-js.mjs`、`npm run check` | 语法和 TypeScript 无输出检查 |
| 预发布启动/验证 | `run_staging.py`、`verify_staging.py` | `runtime/staging/` 或临时隔离目录；fixture/文件邮件 |
| 浏览器回归 | `verify_home_browser.py`、`verify_portal_browser.py`、`verify_qrcode_browser.py`、`verify_scraper_browser.py` | 固定样例；截图进入忽略的 `logs/` |
| 独立服务恢复演练 | `verify_release_systemd.sh` | CI 临时 systemd 服务，不操作生产服务 |
| SSH / 启动采集验收 | `check_ssh_connectivity.py`、`check_startup_source.py` | 有界只读检查；后者要求启动后真实成功 source |
| 通知配置 | `configure_feishu.py`、`feishu_config.py`、`notification_policy_restart.py`、`verify_feishu_runtime.py` | 生产工作流管理配置、探测和必要重启；凭据不输出 |
| 卡片 / 邮件预览 | `preview_feishu_cards.py`、`preview_email.py` | 本地预览；不等同真实客户端或送达证明 |
| 独立运维探活 | `check_ops_health.py` | 私有 watchdog 状态；Telegram 当前待联网启用 |
| 服务器盘点与归档 | `maintain_server_layout.py` | 默认只读；清单与归档放仓库外，见 [目录维护](../docs/operations/DIRECTORY_MAINTENANCE.md) |

新增工具按用途补本表；保持现有 CLI 路径兼容，避免仅为分类移动脚本而破坏工作流。真实数据库、凭据、邮件票据、日志、页面捕获和上传不得写入受版本控制的目录。
