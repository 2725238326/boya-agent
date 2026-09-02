# 生产部署

文档用途：说明从仓库部署一台 BOYA Agent 实例所需的系统、TLS、systemd 和配置步骤。
面向读者：维护服务器的开发者或运维人员。
文档状态：当前部署方案；更新时间：2026-09-02。
事实范围：部署样例和启动代码已核对；当前生产实例使用 `49.233.248.86`、域名 `buaaboya.top`，并已完成 HTTPS、Nginx、systemd 和公开/管理边界验收。SMTP/Telegram 与北航 SSO 仍需业务演练。

## 部署拓扑

```text
浏览器 / 邮件链接
        │ HTTPS :443
        ▼
Nginx（TLS 终止、HTTP 跳转、/admin Basic Auth）
        │ http://127.0.0.1:5000
        ▼
BOYA Agent（Flask + APScheduler，专用 boya-agent 用户）
        ├─ SQLite 数据库
        ├─ Playwright Chromium → 北航课程系统
        ├─ SMTP → 邮件
        └─ Telegram Bot API（可选）
```

当前方案由 Nginx 在本机终止 TLS，再通过 `X-Forwarded-Proto` 传给 Flask。Flask 使用 `ProxyFix` 识别该协议，并只在 HTTPS 请求上设置 `portal_token` 的 `Secure` 属性。邮件链接使用 `APP_PUBLIC_BASE_URL`，不能依赖应用监听地址。

## 上线前条件

- Ubuntu 或兼容的 Linux 主机；Python 3、`python3-venv`、Nginx 和 certbot 已可安装。
- 当前生产 DNS 已将 `buaaboya.top` 指向 `49.233.248.86`；其他实例仍必须核对自己的域名和解析。
- 已准备北航登录凭据、SMTP 应用密码，以及需要时的 Telegram 凭据。
- 已生成随机 `WEB_SECRET_KEY`（至少 32 个字符），并设置管理员认证。服务在缺少这些配置时应拒绝启动。
- 已决定数据库和上传目录的备份位置。

## 部署步骤

### 1. 创建目录并安装应用

在仓库根目录执行：

```bash
bash deploy/setup.sh
```

脚本会把项目复制到 `/home/boya-agent`，创建 `boya-agent` 系统用户和虚拟环境，安装 `requirements.txt`，安装 Playwright Chromium，创建日志/上传目录，并安装 systemd 单元。脚本不会替你申请证书、安装 Nginx 站点或填写 `.env`。

### 2. 填写环境文件

```bash
sudoedit /home/boya-agent/.env
sudo chown root:boya-agent /home/boya-agent/.env
sudo chmod 640 /home/boya-agent/.env
```

至少填写：

```dotenv
BUAA_USERNAME=...
BUAA_PASSWORD=...
WEB_SECRET_KEY=一段新的随机长字符串
ADMIN_USERNAME=...
ADMIN_PASSWORD=...
APP_PUBLIC_BASE_URL=https://你的域名
APP_ALLOWED_ORIGINS=https://你的域名
APP_TIMEZONE=Asia/Shanghai
DATABASE_PATH=/var/lib/boya-agent/data/boya_agent.db
```

邮件、Telegram 和各项业务默认值见 [CONFIGURATION.md](../development/CONFIGURATION.md)。不要把真实 `.env`、SMTP 密码、课程系统密码或 Bot token 提交到 Git。

### 3. 配置 Nginx 与证书

复制 [deploy/nginx_boya.conf](../../deploy/nginx_boya.conf) 到 `/etc/nginx/sites-available/boya-agent`，将 `server_name` 和证书路径替换为现场值，并创建启用链接。证书可以由 certbot 管理；申请和续期命令应按现场 DNS/防火墙方案执行。

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Nginx 样例的要点：

- 80 端口只做 301 跳转到 HTTPS。
- 443 端口转发 `Host`、`X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto`。
- `/healthz` 是公开的最小健康探活端点；详细运行状态 `/api/status` 仍需管理员认证。
- `/admin`、`/api/config`、`/api/status`、`/api/trigger`、`/api/manual-push`、日志、订阅者和 `/api/admin/*` 使用 `.htpasswd`。
- Flask 自己还会校验 `ADMIN_USERNAME/ADMIN_PASSWORD` 或 `ADMIN_API_TOKEN`，所以不能只依赖 Nginx。
- 门户刷新状态使用 `/api/portal/refresh/status`，由用户会话保护；后台完整 `/api/status` 不应公开。

创建 Nginx 管理账号时，账号和密码必须与应用层 Basic Auth 配置保持一致：

```bash
sudo apt-get install apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd_boya 管理员用户名
sudo nginx -t
sudo systemctl reload nginx
```

### 4. 启动并检查 systemd

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now boya-agent
sudo systemctl status boya-agent --no-pager
sudo journalctl -u boya-agent -n 100 --no-pager
```

服务以 `boya-agent` 用户运行，`UMask=027`，不会以 root 身份运行应用。数据库、日志、上传目录和应用目录必须允许该用户访问；`.env` 使用 `root:boya-agent`、`640`，以便服务读取但不向其他用户开放。`ProtectSystem=full` 与 `ReadWritePaths` 已写入单元，若现场把数据库或浏览器数据放到其他目录，需要同步调整权限和单元。

## 上线后验收

以下检查适用于每次更新后的验收：

```bash
curl -I https://你的域名/
curl -I http://你的域名/
curl -i https://你的域名/healthz
curl -u 管理员用户名:管理员密码 https://你的域名/api/status
curl -i https://你的域名/api/status
curl -i https://你的域名/api/courses
```

应确认：HTTP 跳转到 HTTPS；`/healthz` 返回 `200` 和 `status=ok`；未认证的后台状态返回 401；认证后的后台状态返回 JSON；课程页和订阅页可访问；响应包含 `HttpOnly`/`Secure` 会话 Cookie（由 HTTPS 下的验证或登录接口产生）；Nginx 日志中没有把 `.env`、上传目录或数据库暴露为静态文件。

本项目在 2026-09-02 已用 `https://buaaboya.top` 实测：首页、订阅页、门户、RSS/Atom、二维码页和课程 API 返回 200；HTTP 正确跳转到 HTTPS；未认证 `/api/status` 返回 401；认证后管理页和状态接口可用；`boya-agent` 与 Nginx 均为 active。

## 更新原则

更新前先备份 `DATABASE_PATH` 对应 SQLite 文件和 `config/uploads/qrcode/`。当前生产数据库位于 `/var/lib/boya-agent/data/boya_agent.db`。停止服务或确保备份时 SQLite 没有正在写入，再替换代码、重装依赖、执行 `nginx -t`，最后重启服务。应用启动时会执行兼容性增量迁移；本轮新增认证挑战、验证码字段、二维码哈希字段和查询索引，不会删除旧字段。

更新后按 [RUNBOOK.md](RUNBOOK.md) 做健康检查。不要在没有备份和现场确认的情况下删除数据库、上传文件或旧日志。
