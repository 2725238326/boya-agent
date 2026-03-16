# Now

## 项目当前定位

这是一个用于抓取北航博雅课程、做筛选、并通过门户/邮件提供提醒的项目。

当前技术栈：

- 抓取：`Playwright`
- 后端：`Flask`
- 定时调度：`APScheduler`
- 数据库：`SQLite`
- 前端：服务端模板 + 原生 `JS/CSS`

## 当前目录结构

- `src/`
  - `main.py`：应用入口
  - `auth.py`：SSO / WebVPN 登录
  - `scraper.py`：课程列表抓取与详情补全
  - `scheduler.py`：定时任务与推送调度
  - `models.py`：SQLAlchemy 模型
  - `push/email_push.py`：邮件发送与模板
- `web/`
  - `app.py`：Flask 路由与 API
  - `templates/`：页面模板
  - `static/`：门户和控制台前端脚本与样式
- `deploy/`
  - `boya-agent.service`：systemd 服务示例
  - `nginx_boya.conf`：Nginx 反代示例
- `config/.env.example`：环境变量样例

## 部署现状

从仓库配置看，当前预期部署方式是：

- 应用本机运行在 `127.0.0.1:5000`
- `systemd` 服务名：`boya-agent`
- `Nginx` 反向代理到 Flask
- 对外域名：`https://buaaboya.top`

关键配置文件：

- [README.md](E:\Demo\boya-agent\README.md)
- [deploy/boya-agent.service](E:\Demo\boya-agent\deploy\boya-agent.service)
- [deploy/nginx_boya.conf](E:\Demo\boya-agent\deploy\nginx_boya.conf)
- [config/.env.example](E:\Demo\boya-agent\config\.env.example)

## 当前产品面

已存在的主要能力：

- 课程抓取与入库
- 门户查看课程
- 课程筛选
- 选课提醒注册
- 邮件验证 / 登录
- 门户订阅偏好设置
- 控制台管理与手动抓取

## 当前代码状态

仓库当前 `git status` 是干净的，没有未提交改动。

当前代码里值得注意的点：

1. 已经切到 `buaaboya.top`
- Nginx 示例和 `.env.example` 里都已使用新域名
- 邮件链接生成预期也应基于 `APP_PUBLIC_BASE_URL=https://buaaboya.top`

2. 文档和部分前端代码存在历史编码污染
- `README.md` 在当前终端里显示明显乱码
- `portal.js` / `portal.html` / `portal.css` 一类文件里也存在历史中文乱码残留
- 这些问题不一定每处都影响运行，但会增加后续维护风险

3. 门户前端近期是重点风险区
- 门户页承担课程列表、提醒列表、通知中心、设置等多块逻辑
- 历史上这里出现过：
  - 中文字符串损坏
  - 页面跳回 `/subscribe`
  - 提醒计数与列表不一致
  - 刷新状态体验差

4. 抓取链路对“时效性”仍然敏感
- 热门课程名额变化快
- 即使抓取成功，前端如果不显示数据更新时间，也容易误导用户

## 近期已处理过的方向

结合当前代码和最近维护重点，可以确认这几个方向已经被持续处理：

- 门户登录与会话兜底
- 邮件验证链路与新域名切换
- 抓取并发 / 高频巡检
- 门户刷新与提醒链路
- 用户门户首屏与移动端体验

## 仍需持续盯的风险

### 1. 编码问题

这是当前最稳定的技术债之一。

表现：

- 中文在 PowerShell / 部分文件里显示为乱码
- 某些坏字符串会直接导致前端脚本语法错误

建议：

- 后续优先用 UTF-8 无 BOM 统一前端文件
- 每次改 `web/static/*.js` 后都跑一次语法检查
- 不要在终端乱码状态下大面积重写整文件

### 2. 提醒链路分叉

历史上“顶部待提醒数量”和“提醒列表内容”并不总是来自同一数据源。

建议：

- 所有提醒相关展示统一基于同一套提醒序列化结果
- 不要再维护多套 reminder count 口径

### 3. 热门课程的时效问题

热门课可能在几十秒内从“剩余 1 人”变成“已满”。

建议：

- 高频监控保留快抓取模式
- 前端继续保留或增强 `last_seen` / `last_seen_seconds_ago` 展示
- 对 `remaining <= 3` 的课，用更保守的按钮文案和状态提示

### 4. 门户 JS 体量偏大

`web/static/portal.js` 已经承担过多逻辑：

- 会话
- 高亮
- 课程渲染
- 提醒
- 通知
- 设置
- onboarding

建议：

- 后续拆模块，至少按：
  - session
  - courses
  - reminders
  - notifications
  - settings
  划分

## 建议的维护习惯

每次改动后，至少执行：

```powershell
python -m py_compile web/app.py src/scheduler.py src/scraper.py src/models.py
node --check web/static/portal.js
```

如果是线上验证，再补：

```bash
sudo systemctl restart boya-agent
sudo systemctl status boya-agent --no-pager
```

## 如果现在继续维护，优先级建议

1. 先收编码问题
- 尤其是 `web/static/portal.js`、`web/templates/portal.html`、`README.md`

2. 再收门户数据源一致性
- 提醒、通知、顶部统计统一口径

3. 最后再做体验优化
- 移动端布局
- onboarding
- 首屏层级

## 一句话总结

项目主体功能已经齐了，当前不是“从 0 到 1”的问题，而是“把门户前端、提醒链路和编码债收稳”，这样后面迭代才不会反复炸同一块。
