# 安全边界

文档用途：说明用户认证、管理员权限、Cookie、跨站请求、上传和运行文件的安全边界。
面向读者：开发者、运维人员和安全审阅者。
文档状态：当前实现与待确认风险；更新时间：2026-09-02。
相关代码：`web/security.py`、`web/app.py`、`src/models.py`、`web/qrcode_feature.py`。

## 安全目标

本项目处理校园账号、订阅邮箱、门户会话、通知记录和上传图片。安全重点是：不能仅凭邮箱地址登录；普通用户不能调用管理接口；公开二维码不能泄露身份和服务器文件信息；状态修改不能被常见跨站请求直接带 Cookie 触发；生产密钥和运行文件不能使用示例值。

## 用户认证

- `POST /api/login/request` 对已验证且 active 的用户发送一次性登录链接和 6 位验证码；对不存在的邮箱返回相同的泛化成功文案，减少账号枚举。
- `POST /api/subscribe` 对新用户/未验证用户发送一次性验证链接和验证码，对已验证用户发送一次性登录挑战；不直接签发门户 Cookie。
- 一次性链接随机生成，数据库只保存 SHA-256 摘要，具有用途、过期时间、已使用时间，并通过条件更新防止并发重复消费。
- 验证码数据库保存应用密钥 HMAC 摘要，具有过期时间和失败次数上限；超过次数会清除当前验证码。
- 邮箱验证和登录页面的 GET 只显示确认页，POST 才消费挑战，降低邮件安全扫描器提前点击的影响。
- `_current_subscriber()` 只读取 `HttpOnly` Cookie，并要求订阅者 `verified=True`、`active=True`。URL/JSON 中的邮箱、旧登录 token 不参与身份判断。

## 管理员认证

管理页面和管理 API（包括完整运行状态 `/api/status`）同时经过：

1. Nginx `auth_basic`（生产示例为 `/admin`、配置、日志、手动推送、测试邮件、清理、订阅者和 `/api/admin/`）；
2. Flask `web.security` 的 Basic Auth 或 `Bearer ADMIN_API_TOKEN` 校验。

`src/main.py` 和 `web/app.py` 在启动/import 时拒绝少于 32 字符的 `WEB_SECRET_KEY`。生产必须配置 `ADMIN_USERNAME` + `ADMIN_PASSWORD`，或为受控直连自动化配置 `ADMIN_API_TOKEN`；使用 Nginx 示例时仍需准备 `.htpasswd`。

## Cookie 和代理

- 门户 Cookie 名为 `portal_token`，设置 `HttpOnly`、`SameSite=Lax`、180 天有效期；HTTPS 请求下设置 `Secure`。
- `ProxyFix` 只信任一层前置代理的 `X-Forwarded-For/Proto/Host`，Nginx 必须是受控前置代理。
- Nginx 80 端口只做 301 跳转，443 终止 TLS；生产证书路径必须替换为真实证书。
- 浏览器端不再把门户登录 token 放入 `localStorage`；`localStorage` 只保存用户选择的邮箱输入偏好和界面状态。
- Nginx 样例对包含一次性链接、bridge ticket 或邮件操作 token 的 URL 关闭 access log，避免 bearer 值进入反向代理访问日志；真实部署若使用其他网关，也要做同等处理。

## CSRF、CORS 和写请求

- Flask 对 POST/PUT/PATCH/DELETE 检查 `Origin` 或 `Referer`；来源必须等于当前 Host 或 `APP_ALLOWED_ORIGINS` 中的来源。
- 没有这两个请求头的脚本客户端保留兼容性放行，因此部署仍应使用 HTTPS、`SameSite=Lax` 和前置代理；若未来只服务浏览器，可以再收紧为强制 Origin。
- CORS 只有在显式设置 `APP_ALLOWED_ORIGINS` 时才启用，不允许通配来源，也不启用凭据跨域。
- 管理 API 依赖应用层认证，不能把 Nginx 作为唯一安全边界。

## 二维码上传与隐私

- 上传接口要求当前 verified/active 门户会话，贡献者邮箱从 Cookie 对应订阅者取得，忽略客户端提交的邮箱。
- 服务层使用 `secure_filename`、允许扩展名白名单、5 MB 默认上限、读取 +1 字节限制、Pillow `verify()`、实际格式匹配、正数尺寸和 SHA-256 去重。
- 文件名使用日期目录 + 随机名，不使用用户原始文件名作为路径；文件路由拒绝 `..`、空段和非数据库记录路径。
- pending、rejected、expired 或非 active 记录不能从公开列表、排行榜或文件路由读取。
- 公共 DTO 不包含完整邮箱、原始文件名、MIME、服务器路径、哈希或审核字段；管理员接口才可读取私有字段。

## 数据和 secrets

必须保护：

- `boya_agent.db` 及其 `-wal/-shm` 文件；
- `.env`、SMTP 密码、北航账号密码、Telegram token、管理员凭据和 Flask secret；
- `config/uploads/qrcode/`；
- `logs/` 和可能包含外部错误上下文的运行日志。

仓库只保留 `config/.env.example` 占位值。禁止把真实密码、Cookie、完整邮箱、完整 token 或 SMTP 密码写入日志。当前普通运行日志已对邮箱脱敏；数据库业务表仍会保存订阅邮箱，这是产品功能所需的数据，不等同于公开数据。

## 残余风险

1. 邮件退订、暂停和“提醒我”仍使用长期操作 token；它们不建立门户会话，但泄露后可能触发对应动作。
2. 限流目前是单进程内存级按邮箱/IP冷却，重启或多实例不会共享；高风险生产环境应迁移到共享限流存储或前置网关。
3. SQLite 是单实例轻量数据库；不要在没有锁协调的情况下横向扩容 Web/调度进程。
4. 自动选课会使用校园登录会话，默认关闭；启用前需要单独评估账号、封禁和审计风险。
5. 自动选课的 `confirm_before_enroll` 当前是发送提醒而不是阻断式二次确认；虽然已有按业务日失败次数熔断，仍不要把它当作人工审批保护。
6. TLS、证书续期、Cookie 属性和 Nginx 规则仍需在真实服务器用 `nginx -t`、浏览器和 curl 验证。

## 上线前安全检查

- [ ] `.env` 已替换所有示例值，`WEB_SECRET_KEY` 至少 32 字符。
- [ ] Nginx Basic Auth 与应用层管理员凭据已配置，5000 端口不对公网开放。
- [ ] HTTP 自动跳转 HTTPS，证书有效，门户响应包含 Secure Cookie 和 HSTS。
- [ ] 未认证访问 `/api/config`、`/api/status`、`/api/test-email`、`/api/cleanup-expired` 和 `/api/admin/...` 均被拒绝。
- [ ] 普通门户 Cookie 不能读取管理员 API，邮箱地址不能直接建立门户会话。
- [ ] pending 二维码列表和图片地址均不可公开读取；错误扩展名、伪造图片、超限和重复图片均被拒绝。
- [ ] 备份数据库前停止服务，并实际验证备份可读和恢复步骤。
