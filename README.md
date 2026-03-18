# BUAA 博雅课程提醒系统

基于 Playwright 抓取北航博雅选课系统课程信息，提供公开首页、邮件订阅、个人门户和管理后台。

## 当前入口结构

- 公开首页：`/`
- 邮箱订阅：`/subscribe`
- 用户门户：`/portal`
- 签到二维码页：`/QRcode`
- 管理后台：`/admin`

对外应只开放 `80/443`。应用服务默认监听 `127.0.0.1:5000`，由 Nginx 反向代理。

## 核心能力

- 基于 Playwright 的课程抓取
- 邮箱验证码验证与登录桥接
- 用户级提醒、通知与偏好设置
- RSS 输出
- 后台抓取控制、筛选配置、日志与订阅用户管理
- 独立规划中的二维码上传与贡献统计模块

## 项目结构

```text
config/                 运行配置示例与本地配置
src/
  main.py               入口程序
  auth.py               登录鉴权
  scraper.py            课程抓取
  scheduler.py          调度与后台任务提交
  models.py             SQLAlchemy 模型
  push/                 邮件、RSS 等推送实现
web/
  app.py                Flask 路由与 API
  qrcode_feature.py     QRCode 独立模块
  templates/            页面模板
  static/               页面静态资源
deploy/
  nginx_boya.conf       Nginx 示例配置
  admin_access.md       `/admin` 接入说明
```

## 本地启动

1. 创建虚拟环境并安装依赖

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

2. 按 `config/.env.example` 创建 `.env`

关键配置至少包括：

```dotenv
BUAA_USERNAME=xxx
BUAA_PASSWORD=xxx
WEB_SECRET_KEY=replace-with-a-random-secret
WEB_HOST=127.0.0.1
WEB_PORT=5000
APP_PUBLIC_BASE_URL=https://buaaboya.top
```

3. 启动服务

```bash
python src/main.py
```

本地访问：

- 首页：`http://127.0.0.1:5000/`
- 订阅页：`http://127.0.0.1:5000/subscribe`
- 用户门户：`http://127.0.0.1:5000/portal`
- 后台：`http://127.0.0.1:5000/admin`

## 部署建议

- 公网只开放 `80/443`
- Flask 仅监听 `127.0.0.1:5000`
- Nginx 反代应用
- `/admin` 通过 Basic Auth 保护
- 参考 [deploy/nginx_boya.conf](/E:/Demo/boya-agent/deploy/nginx_boya.conf) 和 [deploy/admin_access.md](/E:/Demo/boya-agent/deploy/admin_access.md)

## 数据说明

本次入口重构不涉及数据库迁移。现有订阅用户、课程、提醒、通知和日志数据会继续保留并沿用原表结构。
