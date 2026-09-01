# BUAA 博雅课程提醒系统

基于 Playwright 抓取北航博雅选课系统课程信息，按统一课程状态和用户偏好提供公开课程页、邮箱通知、个人门户和管理后台。

## 当前入口结构

- 公开首页：`/`
- 邮箱订阅：`/subscribe`
- 用户门户：`/portal`
- 签到二维码页：`/QRcode`
- 管理后台：`/admin`

对外只开放 `80/443`。应用服务默认监听 `127.0.0.1:5000`，由 Nginx 终止 TLS、跳转 HTTPS 并反向代理。

## 核心能力

- 基于 Playwright 的课程抓取
- 邮箱一次性链接或 6 位验证码验证和登录
- 用户级提醒、通知与偏好设置
- RSS/Atom 课程内容流
- 后台抓取控制、筛选配置、日志与订阅用户管理
- 二维码上传、审核、过期控制与贡献统计（基础版）
- 自动选课实验能力（默认关闭）

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
docs/                   当前状态、架构、安全、配置、部署和历史资料
```

详细入口见 [docs/README.md](docs/README.md)。当前实现、默认关闭项和待确认风险以 [PROJECT_STATUS.md](docs/project/PROJECT_STATUS.md) 为准。

## 本地启动

1. 创建虚拟环境并安装依赖

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
playwright install chromium
```

生产部署只安装 `requirements.txt`；`requirements-dev.txt` 在此基础上增加测试工具。

2. 按 `config/.env.example` 创建 `.env`

关键配置至少包括：

```dotenv
BUAA_USERNAME=xxx
BUAA_PASSWORD=xxx
WEB_SECRET_KEY=replace-with-a-random-secret-at-least-32-chars
ADMIN_USERNAME=admin
ADMIN_PASSWORD=replace-with-a-long-random-password
WEB_HOST=127.0.0.1
WEB_PORT=5000
APP_PUBLIC_BASE_URL=https://buaaboya.top
```

3. 启动服务

```bash
python src/main.py
```

前端目前继续使用原生 JavaScript 直接加载；TypeScript 7 已作为渐进式、无输出类型检查工具接入。修改前端后可运行：

```bash
npm ci
npm run check
```

本地访问：

- 首页：`http://127.0.0.1:5000/`
- 健康探活：`http://127.0.0.1:5000/healthz`
- 订阅页：`http://127.0.0.1:5000/subscribe`
- 用户门户：`http://127.0.0.1:5000/portal`
- 后台：`http://127.0.0.1:5000/admin`

## 部署建议

- 公网只开放 `80/443`
- Flask 仅监听 `127.0.0.1:5000`
- Nginx 反代应用
- `/admin` 和管理 API 同时通过 Nginx Basic Auth 与 Flask 应用层认证保护
- 参考 [deploy/nginx_boya.conf](/E:/Demo/boya-agent/deploy/nginx_boya.conf) 和 [deploy/admin_access.md](/E:/Demo/boya-agent/deploy/admin_access.md)
- 完整步骤见 [docs/operations/DEPLOYMENT.md](docs/operations/DEPLOYMENT.md)

## 数据说明

启动时会执行兼容性增量迁移，保留现有订阅用户、课程、提醒、通知和日志数据，并为认证挑战、验证码和二维码哈希补充字段。部署前仍应备份数据库；不要把本地 `.env`、数据库、日志或上传文件提交到 Git。
