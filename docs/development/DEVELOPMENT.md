# 开发与测试

文档用途：说明本地开发、验证和扩展项目的最短路径。
面向读者：贡献代码的开发者。
文档状态：当前开发说明；更新时间：2026-09-02。
相关事实：[项目状态](../project/PROJECT_STATUS.md)、[配置](CONFIGURATION.md)、[API 清单](API_INVENTORY.md)。

## 目录职责

```text
src/                    抓取、领域规则、模型、调度和推送实现
src/course_state.py     课程状态、报名窗口、热门和签到标签的唯一规则来源
src/time_utils.py       业务时区和 UTC 转换
src/push/               SMTP、Telegram、RSS/Atom
web/app.py              Flask 页面、API、会话和应用层安全边界
web/security.py         管理员认证、同源检查、安全响应头
web/qrcode_feature.py   二维码页面、公开接口和审核接口
web/templates/          服务端模板
web/static/             无构建步骤的原生 JavaScript/CSS
config/                 配置样例和运行时目录
deploy/                 Nginx、systemd 和部署脚本
scripts/                前端检查和本地邮件预览工具
tests/                  回归、安全、状态、RSS 和二维码测试
docs/                   当前文档、ADR 和历史资料索引
```

当前没有前端打包系统，也没有为了目录美观引入新的框架。修改大文件前，先确认是否能在现有边界内新增 service/helper 和测试。

邮件样式预览：

```bash
python scripts/preview_email.py
```

该脚本只生成本地预览，不发送邮件；邮件预览 HTML 归档在 `docs/archive/email-previews/`。

## TypeScript 7 渐进式检查

仓库通过 `package.json` 固定 TypeScript `7.0.2`，并提交 `package-lock.json`。当前不引入 Vite/Webpack，也不改变浏览器加载方式；`tsconfig.json` 使用 `noEmit`，先对 `web/static/subscribe_bridge.js` 开启 `checkJs` 作为迁移样板。后续每扩大一批检查范围，都应先修复类型问题并运行完整前端检查。

```bash
npm ci
npm run check
```

`npm run check` 会检查 `web/static/` 下所有 JavaScript 的语法，并运行 TypeScript 7 类型检查。生产机只需在需要执行前端检查时安装 Node.js 依赖，当前服务运行本身不依赖 TypeScript。

## 创建开发环境

```bash
python -m venv venv
source venv/bin/activate
python -m pip install -r requirements-dev.txt
playwright install chromium
```

Windows PowerShell 可使用：

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
playwright install chromium
```

生产服务只需要 `requirements.txt`；`requirements-dev.txt` 在其基础上增加 pytest，用于本地和 CI 测试。

复制 `config/.env.example` 为 `.env`，至少设置随机 `WEB_SECRET_KEY` 和管理员认证；直接导入 `web.app` 也会检查密钥长度。真实北航、SMTP 和 Telegram 凭据只放在本地或服务器，不放入测试文件。

## 常用验证命令

```bash
python -m compileall -q src web tests
python -m pytest -q
```

检查所有静态 JavaScript：

```bash
find web/static -name '*.js' -print0 | xargs -0 -n1 node --check
```

PowerShell：

```powershell
Get-ChildItem web/static -Filter *.js -Recurse | ForEach-Object { node --check $_.FullName }
```

跨平台前端检查：

```bash
npm run check
```

模板中的内嵌脚本不经过 Node 单文件检查，修改模板后还要在浏览器中打开对应页面并检查控制台、网络响应和空状态。当前工作区的完整 pytest 结果以最终报告为准；如果依赖没有安装，必须报告收集阶段的真实错误，不得用跳过测试冒充通过。

## 新增或修改 API

1. 先在 [API_INVENTORY.md](API_INVENTORY.md) 判断公开、用户会话、管理员和敏感数据边界。
2. 对 Cookie 会话状态修改保留同源检查；输入做类型、长度、范围和枚举校验。
3. 保持现有 `{success, data, error}` 兼容结构，新增稳定的错误码时同时更新前端和文档。
4. 用户响应不要返回完整 token、密码、验证码、服务器路径或不必要的邮箱；日志只写脱敏邮箱。
5. 管理 API 必须经过 `web.security.requires_admin()`，不要在每个路由复制一套认证判断。
6. 增加正向、未授权、过期/重复使用和异常分支测试，再运行编译、JavaScript 和 pytest 检查。

## 数据库变更

`src/models.py` 使用 SQLAlchemy 模型和轻量增量迁移。新增字段时：

- 先记录旧表可能存在的列，再写幂等的 `ALTER TABLE` 兼容迁移；
- 不删除旧字段、不覆盖已有业务数据；
- 用空数据库测试 `Base.metadata.create_all()` 和 `init_db()`；
- 用旧 schema 测试升级路径；
- 为新字段的默认值、时区、敏感性和索引写进配置/安全文档；
- 不在开发测试中触碰工作区的真实 `boya_agent.db`。

认证挑战和验证码只保存摘要或受保护的摘要；二维码公开接口用 `to_dict()` 的公开字段，审核接口才可使用私有字段。

## 修改课程规则或推送

课程是否结束、是否可报名、自主签到和热门判断只能在 `src/course_state.py` 增加或修改。筛选、抓取、门户、邮件、Telegram、RSS 和二维码复用该模块，不在调用方重新判断。

发送新通知时，把“选择收件人”“决定事件/时间窗口”“发送渠道”“记录结果”分开。检查 `email_enabled`、`telegram_enabled`、`rss_enabled` 和每日摘要开关的实际语义，并为开关关闭、发送成功、发送失败和重复事件分别测试。

## 提交前清单

- `git diff --check` 无空白错误，且没有把 `.env`、数据库、日志、上传文件加入 diff。
- `python -m compileall -q src web tests` 通过。
- 所有相关 `node --check` 通过。
- 新测试覆盖权限、过期、重复使用、无数据和异常路径。
- 文案仍面向普通用户，内部状态名只出现在工程文档或技术响应中。
- 当前事实、配置和部署变化已更新对应权威文档；历史文档只加替代链接，不复制新的默认值。
