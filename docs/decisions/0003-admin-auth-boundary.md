# ADR 0003：管理员采用应用层与 Nginx 双层边界

- 状态：已采用
- 日期：2026-09-02
- 相关代码：`web/security.py`、`web/app.py`、`deploy/nginx_boya.conf`

## 背景

只在 Nginx 配置 Basic Auth 无法保护直接访问 Flask 端口、测试环境或未来的其他反向代理。管理页面、配置、触发抓取、日志、订阅者和自动选课接口还需要统一、可测试的应用层权限判断。

## 决定

`web.security.requires_admin()` 在 Flask `before_request` 统一识别管理页面和接口，支持应用层 Basic Auth 或 Bearer `ADMIN_API_TOKEN`。Nginx 继续对生产管理路径启用 `.htpasswd`，并转发 `Authorization`。完整 `/api/status` 也属于管理员；门户只调用会话保护的 `/api/portal/refresh/status`，不公开后台统计。

所有浏览器状态修改请求检查 `Origin`/`Referer` 同源或显式允许来源；没有这两个头的脚本调用保持兼容。CORS 只有设置 `APP_ALLOWED_ORIGINS` 时才启用，且不允许通配来源或跨域凭据。

## 后果

- 每个新管理路由都必须进入集中边界和 Nginx 清单，并补未授权测试。
- 生产管理员需要同时维护 Nginx `.htpasswd` 与应用层凭据；两者不一致时请求会被拒绝。
- 无请求来源头的 CLI 仍可调用，因此 HTTPS、端口隔离和管理员凭据保护不能省略。
