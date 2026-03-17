# /admin 后台接入说明

目标：
- 对外只保留 `80/443`
- Flask 仅监听 `127.0.0.1:5000`
- 后台统一走 `https://你的域名/admin`
- 通过 Nginx Basic Auth 输入账号密码进入

## 1. 确认应用只监听本机

在部署目录的 `.env` 中确认：

```dotenv
WEB_HOST=127.0.0.1
WEB_PORT=5000
APP_PUBLIC_BASE_URL=https://buaaboya.top
```

改完后重启服务：

```bash
sudo systemctl restart boya-agent.service
```

## 2. 创建后台账号密码

如果服务器没有 `htpasswd`，先安装：

```bash
sudo apt-get update
sudo apt-get install -y apache2-utils
```

创建密码文件：

```bash
sudo htpasswd -c /etc/nginx/.htpasswd_boya admin
```

说明：
- `admin` 可以换成你自己的用户名
- 首次创建使用 `-c`
- 后续新增或修改账号不要再带 `-c`，否则会覆盖原文件

## 3. 应用新的 Nginx 配置

把仓库里的 [nginx_boya.conf](/E:/Demo/boya-agent/deploy/nginx_boya.conf) 同步到服务器对应站点配置后，检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 4. 关闭公网 5000

除了把应用监听改成 `127.0.0.1`，还要同步检查：
- 云服务器安全组
- 系统防火墙 `ufw` / `firewalld`

确保外部不能直接访问 `5000/tcp`。

## 5. 最终访问方式

公开入口：

```text
https://buaaboya.top/
```

后台入口：

```text
https://buaaboya.top/admin
```

访问 `/admin` 时浏览器会弹出账号密码框，输入你在 `htpasswd` 里配置的账号密码即可。

## 6. 上线后自检

建议至少确认这几件事：

```bash
curl -I http://127.0.0.1:5000
curl -I https://buaaboya.top/
curl -I https://buaaboya.top/admin
```

预期：
- 本机 `127.0.0.1:5000` 可访问
- 公开首页正常返回
- `/admin` 返回 `401 Unauthorized`，浏览器输入账号密码后可进入
