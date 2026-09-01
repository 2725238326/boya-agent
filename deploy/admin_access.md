# `/admin` 后台接入说明

> 文档状态：部署补充资料。认证、TLS、systemd 和上线检查的完整当前步骤以 [docs/operations/DEPLOYMENT.md](../docs/operations/DEPLOYMENT.md) 为准；本文不替代应用层管理员认证说明。

目标：
- 对外只保留 `80/443`
- Flask 仅监听 `127.0.0.1:5000`
- 后台统一走 `https://你的域名/admin`
- 通过 Nginx Basic Auth 输入账号密码进入；Flask 还会校验应用层 `ADMIN_USERNAME/ADMIN_PASSWORD` 或 `ADMIN_API_TOKEN`

## 1. 确认应用只监听本机

部署目录的 `.env` 中至少应包含：

```dotenv
WEB_HOST=127.0.0.1
WEB_PORT=5000
APP_PUBLIC_BASE_URL=https://buaaboya.top
```

修改后重启服务：

```bash
sudo systemctl restart boya-agent.service
```

## 2. 创建后台账号密码

如果服务器还没有 `htpasswd`：

```bash
sudo apt-get update
sudo apt-get install -y apache2-utils
```

创建后台密码文件：

```bash
sudo htpasswd -c /etc/nginx/.htpasswd_boya admin
```

说明：
- `admin` 可以改成你自己的用户名
- 首次创建使用 `-c`
- 之后新增或修改账号时不要再带 `-c`，否则会覆盖原文件

## 3. 应用新的 Nginx 配置

将仓库中的 [deploy/nginx_boya.conf](/E:/Demo/boya-agent/deploy/nginx_boya.conf) 同步到服务器站点配置后，执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 4. 关闭公网 5000 端口

除了把应用监听地址改为 `127.0.0.1`，还要同步检查：
- 云服务器安全组
- 系统防火墙，例如 `ufw` 或 `firewalld`

确保外部无法直接访问 `5000/tcp`。

## 5. 最终访问方式

公开首页：

```text
https://buaaboya.top/
```

后台入口：

```text
https://buaaboya.top/admin
```

访问 `/admin` 时浏览器会弹出账号密码框，输入 `htpasswd` 里配置的账号密码即可。

## 6. 上线后自检

建议至少确认以下结果：

```bash
curl -I http://127.0.0.1:5000
curl -I https://buaaboya.top/
curl -I https://buaaboya.top/admin
```

预期：
- `127.0.0.1:5000` 仅服务器本机可访问
- 公开首页正常返回
- `/admin` 未认证时返回 `401 Unauthorized`
- 浏览器输入账号密码后可正常进入后台
