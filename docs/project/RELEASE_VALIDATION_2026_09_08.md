# 2026-09-08 发布验证

## 应用发布

`0b44182` 已通过功能分支 CI、生产工作流并部署到 `49.233.248.86`，访问入口为 `https://buaaboya.top`。服务与 Nginx 为 active，服务器工作树无变更。

- 邮件默认关闭：验证码、通知及管理端发送共用底层策略。暂停时不领取邮件任务，不消耗其重试次数，不创建新课程邮件任务。
- 暂停状态的登录和订阅请求返回 HTTP 503，不再显示发送成功。管理端认证仍优先执行。
- 空课程页面不再断言“不是系统错误”。
- 部署工作流不再因后续提交而取消正在运行的部署。
- 未迁移数据库，未更换框架，未发送测试邮件或恢复邮件渠道。

## 验证记录

本地使用 `D:\Anaconda\python.exe scripts/verify_release.py`：70 项测试通过、1 项跳过；六个 JavaScript 文件语法检查与 TypeScript 7 检查通过。跳过项不计为验证通过。

- [功能分支 CI](https://github.com/2725238326/boya-agent/actions/runs/34177364908)：成功。
- [生产部署](https://github.com/2725238326/boya-agent/actions/runs/34177420064)：成功，包含备份完整性、服务启动与公开页面检查。
- 部署后再次检查：公开 `/healthz` 返回成功；空请求访问 `/api/login/request` 返回 `503 email_delivery_paused`，没有提供收件人。
- 数据库备份：`/var/lib/boya-agent/backups/boya_agent-20260908-094102-pre-0b44182.db`。

## 浏览器固定样例

新增 `scripts/verify_portal_browser.py`。运行前安装 Playwright Chromium：`python -m playwright install chromium`；随后执行 `python scripts/verify_portal_browser.py`。

本地 Chromium 的 390px、1280px 视口均通过无课、加载失败、重试恢复、筛选无结果及清除筛选检查；没有横向溢出、未捕获脚本异常，Tab 能移入页面控件。所有 HTTP 请求均由固定样例响应，不访问校园系统、生产服务器或 SMTP。此脚本也接入功能分支 CI。

## 发布限制

这批验证不覆盖真实课程变化、全流程屏幕阅读器操作或长期并发负载。门户主脚本尚未全部纳入 TypeScript 检查。本地仍使用现有 Anaconda 环境，尚未建立与 CI 一致的锁定依赖环境。性能尚无新的前后对比数据，不宣称提速。

下一批按实际风险推进：统一开发环境；补充有课、已满和提醒设置样例；测量接口与抓取耗时后再调整慢路径。邮件恢复另行确认。

## 回退约束

上一版本 `b5e74c8` 没有邮件总开关，直接回退会失去验证码及管理员发送保护。需要回退时保留 `src/email_policy.py` 和所有调用点，或先停止应用；不能仅靠数据库渠道选项保证全部邮件关闭。此次无结构迁移，不需要例行恢复数据库备份。
