# BUAA 博雅课程提醒系统｜项目总览与实现细节

> 文档状态：本轮重构前的静态审计快照，不代表修改后的当前实现。当前事实以 [docs/README.md](docs/README.md) 及其链接的主文档为准；本文正文保留用于追溯，不应直接复制其中的旧结论。

> 生成日期：2026-09-02  
> 代码基线：main 分支，提交 b89737a（2026-04-30）  
> 资料范围：仓库内说明文档、Python/JavaScript/Nginx/systemd 配置、测试用例和数据库模型。  
> 判定口径：以当前代码和当前配置为准；历史文档中的规划、旧部署信息和实现不一致之处单独标注。

## 0. 执行摘要

BUAA 博雅课程提醒系统的核心价值，是把北航博雅课程系统中的课程信息抓取出来，经过课程状态和用户偏好筛选后，通过门户和邮件提醒用户关注新课、退课捡漏、临近开课和待签到课程。

当前代码已经形成一条较完整的产品链路：

课程系统登录与抓取 → DOM/网络结果解析 → 去重、健康检查和生命周期管理 → SQLite 存储 → 全局及用户级过滤 → 邮件提醒、摘要和课程提醒 → 用户门户查询与偏好管理 → 管理后台运维。

二维码上传/展示模块也已经存在于当前代码中，但审核、过期、去重和文件内容校验尚未完善。自动选课仍属于可选的实验能力，默认关闭。

最需要优先处理的不是继续扩展功能，而是以下上线安全和一致性问题：

1. 用户登录接口目前可通过“已注册邮箱”直接建立门户会话，没有再次证明邮箱所有权。
2. Nginx 的兜底路由是公开的，清理过期数据和测试邮件接口没有被显式纳入 Basic Auth 保护。
3. 二维码上传记录在提交后可进入公开列表，且公开响应中仍带有原始贡献者邮箱字段。
4. 文档描述的三分钟抓取、持久化浏览器会话和当前代码的十分钟配置、进程内会话并不一致。

仓库内的 boya_agent.db 当前为空文件，无法从仓库确认线上课程数量、订阅人数、发送成功率或实际运行稳定性。下面的结论是静态代码和配置审查结果，不等同于线上运行报告。

## 1. 项目定位与当前状态

### 1.1 面向用户的问题

北航博雅课程存在信息更新快、选课窗口短、退课后名额重新出现不易发现等问题。系统通过高频抓取、筛选和分级提醒降低用户持续刷新课程系统的成本。

### 1.2 当前能力矩阵

| 能力 | 当前状态 | 实现位置 | 说明 |
| --- | --- | --- | --- |
| 博雅课程抓取 | 已实现 | src/auth.py、src/scraper.py、src/scheduler.py | Playwright 登录、分页抓取、DOM 快路径、网络响应补充、full/quick 两种模式 |
| 课程持久化 | 已实现 | src/models.py、src/scraper.py | SQLite、WAL、重复课程更新、生命周期和过期清理 |
| 课程筛选 | 已实现 | src/filters.py、src/course_state.py | 自主签到、校区、关键词、剩余名额、白名单/黑名单、优先级评分 |
| 邮箱订阅与验证 | 已实现 | web/app.py、src/push/email_push.py | 验证码、验证链接、取消订阅、暂停推送 |
| 门户登录桥接 | 已实现但存在高风险 | web/app.py、web/static/portal.js | HttpOnly Cookie、跨设备 bridge ticket、长期会话辅助；邮箱登录接口当前不验证邮箱所有权 |
| 个性化提醒 | 已实现 | src/push/email_push.py、src/scheduler.py | 用户过滤、优先提醒、紧急/近期/每日摘要、去重、暂停 |
| 用户门户 | 已实现 | web/templates/portal.html、web/static/portal.js | 课程、通知时间线、提醒管理、偏好设置、暂停和退订 |
| 管理后台 | 已实现，依赖反向代理鉴权 | web/templates/index.html、web/static/app.js | 手动抓取、配置、自动选课、订阅用户、推送和报名日志 |
| RSS | 已实现 | src/push/rss_feed.py、web/app.py | /rss 可生成课程 RSS；配置中的 rss_enabled 当前不控制该公开接口 |
| Atom | 部分实现 | src/push/rss_feed.py | 有课程数据且 feedgen 可用时存在未定义变量，端点可能报错 |
| 二维码上传/展示 | 已实现但治理不完整 | src/qrcode_service.py、web/qrcode_feature.py | 公共大厅、课程上下文、排行榜、上传和预览；当前缺少可靠审核链路 |
| Telegram | 部分实现 | src/push/telegram_bot.py、src/scheduler.py | 主要用于连续失败告警、提醒和自动选课结果；常规用户课程广播已改为邮件为主 |
| 自动选课 | 实验能力，默认关闭 | src/enroll.py、src/filters.py、src/scheduler.py | 需要打开配置并承担账号、代理和封禁风险 |
| 统计和运营分析 | 基础能力 | web/app.py、src/models.py | 有课程热度、推送、订阅用户和二维码贡献统计；没有完整监控指标体系 |

### 1.3 文档与实现的状态差异

| 文档描述 | 当前实现 | 结论 |
| --- | --- | --- |
| README.md 和 now.md 将二维码功能描述为后续规划 | 当前已有 QRcode 页面、上传接口、课程上下文和排行榜 | 二维码应从“规划”调整为“已上线基础版，待治理完善” |
| CONTEXT.md 描述三分钟高频刷新 | config/.env.example、default_config.json 和当前调度默认值为十分钟 | 以环境配置为准；需统一产品说明和运营预期 |
| CONTEXT.md 描述持久化浏览器会话 | scraper.py 使用普通 headless context，storage_state 为 None；仅在进程内复用浏览器对象 | 重启进程后可能丢失 SSO 会话，当前不是真正的跨重启持久化 |
| change-3-07.md 记录门户尚无暂停等能力 | 当前已有 push_paused_until、暂停/恢复接口和门户控制项 | 该文档是历史迭代记录，不应作为当前能力清单 |
| CONTEXT.md 中仍有旧域名和旧部署目录 | 当前 Nginx 和 .env.example 使用 buaaboya.top，服务示例路径为 /home/boya-agent | 旧部署信息需要归档，避免误操作 |
| now.md 提到 app.js 重复 helper | 当前静态扫描未发现重复的顶层 helper 名称，但 portal.js 仍有乱码和维护痕迹 | 该项可能已经部分收敛，仍应做一次前端清理 |

## 2. 用户、管理员与主要使用流程

### 2.1 普通用户流程

1. 用户从首页进入订阅页，提交邮箱。
2. 系统创建或恢复订阅记录，发送四位验证码和验证链接。
3. 用户完成验证后获得门户会话，可通过验证链接、验证码或 bridge ticket 进入门户。
4. 门户加载课程列表、今日新增、未来课程、热门/紧张课程和通知时间线。
5. 用户设置校区、课程类别、自主签到、活动状态等偏好，并可针对课程设置提醒。
6. 推送服务依据用户过滤条件、课程剩余名额、开课时间和最近发送记录发送邮件。
7. 用户可以暂停推送、恢复推送、退出当前设备或取消订阅。

### 2.2 管理员流程

1. Nginx 对 /admin 及管理 API 进行 Basic Auth。
2. 管理员查看运行状态、抓取健康、浏览器状态、推送缓冲区和日志计数。
3. 管理员修改筛选、调度、每日摘要和自动选课配置。
4. 管理员手动触发 full/quick 抓取，或查看抓取结果。
5. 管理员查看订阅用户、暂停状态、门户活跃度、发送统计和待提醒数据。
6. 管理员发送服务更新、手动推送或执行过期数据清理。

### 2.3 核心数据流

抓取调度器按周期启动任务，任务复用或重建 Playwright 浏览器，完成 WebVPN/SSO 登录后抓取多个课程视图。解析器先走可见 DOM 快路径，结果过少时再走 Locator 回退，并补充已记录的网络响应。课程经过身份归一化和去重后进入健康检查；如果快照明显稀疏，则阻止覆盖数据库。正常结果会更新课程、计算退课捡漏和生命周期状态，再进入过滤器和推送调度。

课程提醒链路与新课推送链路分开：

- 新课和退课捡漏：进入 priority、digest_urgent、digest_soon 或 digest_daily。
- 临近报名窗口的活跃课程：由 active watcher 以 quick 抓取和专门去重策略处理。
- 热门课程：由 hot watcher 更短周期地 quick 抓取，但仅在课程接近满额或剩余很少时触发。
- 用户设置的课程提醒：每分钟检查一次，成功发送后标记为已发送。

## 3. 系统架构

### 3.1 组件关系

| 层次 | 组件 | 职责 |
| --- | --- | --- |
| 入口 | src/main.py | 加载环境变量、初始化日志和数据库、启动 Flask、创建运行时事件循环、启动调度器 |
| 抓取 | src/auth.py | WebVPN/SSO 登录、登录状态判断、登录异常截图和重试 |
| 抓取 | src/scraper.py | 浏览器上下文、课程视图遍历、DOM/网络解析、字段归一化、去重、健康检查、持久化 |
| 调度 | src/scheduler.py | 周期任务、浏览器复用和回收、活跃/热门监控、推送缓冲、生命周期、每日任务 |
| 领域逻辑 | src/course_state.py、src/filters.py | 签到标签、可报名状态、热门判断、全局筛选和自动选课候选排序 |
| 数据层 | src/models.py | SQLAlchemy 模型、SQLite 引擎、WAL、增量迁移和重试提交 |
| 通知 | src/push/email_push.py | SMTP、代理、验证邮件、课程通知、摘要、去重和通知事件 |
| 通知 | src/push/telegram_bot.py、src/push/rss_feed.py | Telegram 告警/辅助通知、RSS/Atom 生成 |
| 自动选课 | src/enroll.py | 课程选课动作和报名结果记录 |
| Web | web/app.py | 页面、公共 API、门户 API、管理 API、会话和订阅状态 |
| Web | web/qrcode_feature.py | 二维码页面、课程上下文、上传和公开列表 |
| 前端 | web/static/*.js、web/static/*.css | 首页、管理后台、门户、订阅桥接和二维码大厅交互 |
| 外部代理 | Nginx | 反向代理、Basic Auth、上传大小和超时控制 |
| 运行托管 | deploy/boya-agent.service | systemd 常驻进程和自动重启 |

### 3.2 进程启动和运行模式

src/main.py 的默认运行路径如下：

1. 加载根目录 .env 和 config/.env。
2. 设置 Loguru 控制台日志和 logs/boya_agent_日期.log 文件日志。
3. 执行 init_db，创建缺失表并做增量字段迁移。
4. 启动 Flask 线程和独立的 asyncio 运行时循环。
5. 按 SCRAPE_INTERVAL_MINUTES 创建调度任务。
6. 启动后先执行一次 full 抓取。
7. 主线程保持运行，退出时尝试关闭浏览器。

传入 --once 时只执行一次抓取后退出，适合手工诊断或一次性任务。

## 4. 抓取、身份和数据质量

### 4.1 登录

登录目标是北航 WebVPN 下的博雅选课系统，核心地址在 src/auth.py 中配置为 d.buaa.edu.cn 和 BYKC 的编码路径。登录逻辑能够识别 SSO/CAS 页面，填写 iframe 中的账号密码，支持多个选择器和最多三次尝试，并在异常时记录截图。

登录判定综合当前 URL、页面标题和校园网阻断提示。抓取需要 BUAA_USERNAME 和 BUAA_PASSWORD；仓库只提交了 .env.example 占位配置，没有发现真实凭据。

### 4.2 课程采集

scraper.py 支持以下策略：

- all、近期课程、即将开课/开抢、远期课程/预告课程等多个课程视图。
- 每个视图最多遍历 25 页，按课程表头别名识别真实表头。
- 优先使用 page.evaluate 解析可见表格，处理 colspan 和 rowspan。
- DOM 结果不足时使用 Locator 回退。
- 记录网络响应，从 JSON/嵌套对象中补充课程行。
- full 模式获取课程详情页中的签到方式、类别、组织方、简介等字段；quick 模式跳过详情，降低高频监控成本。
- 对不同校区、地点和时间的并行开课保留独立课程记录。

### 4.3 课程身份和去重

课程 ID 由规范化后的课程名、时间、教师、地点和校区等字段生成，并保留 legacy hash 兼容历史数据。保存时会：

- 合并同一课程的不同抓取视图；
- 保留不同地点/校区的平行开课；
- 清理时间轻微漂移造成的近重复记录；
- 在多个快照中优先保留剩余名额更有价值的记录；
- 将已结束或报名窗口已过的课程标记为 expired，而不是立即删除；
- 跳过抓取到时已经结束的新课程；
- 过期超过 30 天后由凌晨清理任务移除。

### 4.4 健康检查和异常快照防护

系统比较本次抓取数量与数据库活跃课程基线。以下情况会被视为可疑：

- 快路径行数低于 SCRAPER_MIN_FAST_PATH_ROWS；
- 总行数低于 SCRAPE_HEALTH_MIN_ROWS；
- 当前数量明显低于历史基线，比例阈值由 SCRAPE_HEALTH_RATIO 控制；
- 没有足够的未来课程、开放课程或可用名额。

健康检查失败时，不覆盖数据库，避免登录异常、页面空白或表格结构变化导致全量课程被误删。任务在抓取异常时会重建浏览器并重试一次；连续失败达到三次会通过 Telegram 发送状态告警。

### 4.5 浏览器会话的实际边界

代码中定义了 BROWSER_DATA_DIR，但 create_browser_context 使用的是普通 browser.new_context，storage_state 为 None，没有调用 launch_persistent_context。因此：

- 单个进程内可以复用 browser/context/page；
- 浏览器达到运行次数阈值后会回收重建；
- 进程重启后不会自动从 browser_data 恢复 Cookie；
- SSO 会话是否能跨重启继续，取决于远端行为，而不是本地持久化设计。

如果产品确实要求跨重启免登录，应补充持久化上下文、Cookie 文件权限、过期和清理策略，并评估保存校园账号会话的安全影响。

## 5. 课程状态与筛选逻辑

### 5.1 课程状态

course_state.py 统一计算以下语义：

| 语义 | 规则摘要 |
| --- | --- |
| 自主签到 | 综合 check_in_method 和 sign_method；文本包含“自主”时归为自主签到 |
| 可报名 | 当前时间位于报名窗口内，课程未结束、未过期 |
| 热门课程 | 可报名且有容量，剩余名额不超过 HOT_COURSE_REMAINING_THRESHOLD，或填充率达到 HOT_COURSE_FILL_RATIO |
| 已结束 | 课程结束时间已过 |
| 已过期 | 课程状态或报名窗口已过，最终由保存和生命周期任务同步 |

默认热门阈值为剩余 3 个名额或填充率 82%，可通过环境变量调整并被限制在合理范围内。

### 5.2 全局筛选

filters.py 支持：

- 类别精确匹配；
- 仅自主签到；
- 严格博雅模式，排除组织方带有校医院等非目标课程；
- 最少剩余名额；
- 校区模糊匹配；
- 课程名/类别白名单和黑名单；
- 优先关键词加权；
- 报名时间紧迫度和剩余名额评分；
- 自动选课候选的最大每日次数、确认开关和优先级排序。

### 5.3 用户级筛选

邮件订阅者拥有独立的类别、校区和自主签到偏好，并可暂停推送。用户必须已验证且 active，暂停窗口内不会发送普通课程通知。

当前存在一个需要统一的实现差异：全局 filters.py 和 course_state.py 会综合签到方式与 sign_method，并识别“自主/自选”等文本；email_push.py 的用户筛选主要只检查 check_in_method 中是否包含“自主”。因此同一门课在门户筛选可见、但用户邮件筛选中可能被排除。

## 6. 推送和提醒

### 6.1 事件类型

| 事件 | 触发条件 | 发送模式 |
| --- | --- | --- |
| new | 新课程首次入库并通过过滤 | priority 或按时间进入摘要缓冲 |
| snipe | 旧课程从无剩余恢复为有剩余 | priority，按最近信号去重 |
| active_watch | 报名窗口活跃、刚开始或仍有足够名额 | active_watch，独立于普通新课去重 |
| course_reminder | 用户为课程设置的提前提醒 | reminder |
| service_update | 管理员发给订阅用户的服务更新 | service_update |
| daily summary | 开启每日摘要后的符合条件课程集合 | digest_daily |

### 6.2 时间分层

代码注释和历史文档将课程按距离开课时间分层：

- 一小时以内：立即 priority；
- 一小时至十二小时：进入 urgent 缓冲；
- 十二小时至二十四小时：进入 soon 缓冲；
- 超过二十四小时：通常进入每日摘要。

实际缓冲刷新间隔由 URGENT_DIGEST_MINUTES 和 SOON_DIGEST_MINUTES 控制，当前示例配置分别为 5 分钟和 30 分钟。SCRAPE_INTERVAL_MINUTES 当前示例值为 10 分钟。历史文档中出现的“三分钟抓取、三小时/十二小时摘要”等描述需要统一。

### 6.3 邮件发送

email_push.py 支持：

- SMTP 主通道和验证/通知分离通道；
- Gmail、QQ 等不同 TLS/端口配置；
- HTTP CONNECT 代理；
- 失败重试和通知通道回退；
- HTML 邮件中的课程详情、退订、暂停和门户链接；
- 按订阅者偏好筛选；
- priority、snipe、active_watch 和普通摘要去重；
- 每次按课程记录 notification_events。

config/default_config.json 中 email_enabled 默认是 false，因此即使抓取和门户正常，未开启邮件配置也不会向普通用户发送课程邮件。

### 6.4 Telegram 和 RSS 的实际边界

调度器的 _do_push 只在 email_enabled 开启时执行用户邮件发送，regular 课程广播不再依赖 telegram_enabled。Telegram 目前主要用于：

- 连续抓取失败的管理员告警；
- 课程提醒；
- 自动选课确认和结果。

RSS/Atom 是公开读取接口，RSS 生成逻辑存在；FilterConfig 中的 rss_enabled 当前没有作为公开路由开关使用。若需要真正支持“关闭 RSS”，应在路由或生成器入口补充控制。

### 6.5 提醒去重和暂停

课程提醒每分钟扫描待发送记录。课程在提醒窗口内且用户仍 active 时发送；发送成功才会标记为 sent。用户可以通过门户或邮件链接暂停推送、恢复推送、退出当前设备和取消订阅。

通知时间线读取 notification_events，支持按时间范围、new/snipe 类型、发送成功状态和关键词过滤，并可以在门户端导出 CSV。

## 7. 页面与 API 总览

### 7.1 页面

| 路径 | 用途 | 访问边界 |
| --- | --- | --- |
| / | 公开首页、系统状态摘要和入口 | 公开 |
| /subscribe | 邮箱订阅、验证码验证和已注册用户入口 | 公开 |
| /verify/<token> | 验证链接入口 | 公开 |
| /portal | 用户门户 | 依赖门户 Cookie 或 token |
| /QRcode | 二维码共享大厅 | 公开 |
| /QRcode/course/<course_id> | 课程上下文二维码页 | 公开，课程不存在时 404 |
| /admin | 管理后台 | 依赖 Nginx Basic Auth |
| /console | 兼容旧入口，重定向到管理后台 | 应纳入同等保护范围 |
| /rss、/atom | 课程订阅源 | 公开 |

### 7.2 公开和课程 API

| 方法与路径 | 作用 |
| --- | --- |
| GET /api/courses | 课程查询，支持类别、校区、关键词、自主签到、可用/候补/过期等过滤 |
| GET /api/public/insights | 公开统计：活跃课程、可用课程、热门课程和下一批报名信息 |
| GET /api/categories | 返回课程类别 |
| GET /api/status | 运行状态、课程总数、可用数、热门数、今日新增、推送和浏览器状态 |
| POST /api/portal/refresh | 为门户触发 quick 后台刷新 |
| GET /api/portal/highlights | 门户首页摘要和重点课程 |
| GET /rss、GET /atom | 生成 RSS/Atom |

### 7.3 订阅、登录和会话 API

| 方法与路径 | 作用 |
| --- | --- |
| POST /api/subscribe | 创建或恢复订阅、发送验证邮件 |
| POST /api/subscribe/verify-code | 校验四位验证码 |
| POST /api/login/request | 已注册邮箱登录入口；当前实现直接按邮箱建立门户 Cookie，存在邮箱所有权校验缺失 |
| GET /api/login/<token> | 使用已验证订阅 token 登录 |
| GET /api/verify/<token> | 验证链接并重定向 |
| GET /api/subscribe/bridge/<ticket>/status | 查询跨设备桥接状态 |
| POST /api/subscribe/bridge/<ticket>/claim | 领取桥接票据并建立会话 |
| POST /api/subscriber/lookup | 查询订阅状态 |
| GET/PUT /api/subscriber/session | 读取或更新当前用户会话、偏好和活跃时间 |
| POST /api/subscriber/session/onboarding-seen | 标记门户引导已查看 |
| POST /api/session/clear | 清除当前门户会话 |
| GET/POST /api/unsubscribe、GET /api/unsubscribe/<token> | 取消订阅 |
| GET/POST /api/pause/<token>、POST /api/subscriber/session/pause-push | 暂停推送 |
| POST /api/subscriber/session/resume-push | 恢复推送 |

### 7.4 门户 API

| 方法与路径 | 作用 |
| --- | --- |
| GET /api/subscriber/session/reminders | 当前用户待提醒 |
| GET /api/subscriber/<token>/reminders | token 方式读取提醒 |
| GET /api/subscriber/session/notifications | 当前用户通知时间线 |
| GET /api/subscriber/<token>/notifications | token 方式读取通知时间线 |
| POST /api/remind/<course_id> | 当前会话为课程创建提醒 |
| GET/POST /api/remind/<token>/<course_id> | token 方式读取或创建课程提醒 |
| GET /api/portal/highlights | upcoming 24h、今日新增、待提醒和重点课程 |

### 7.5 管理 API

| 方法与路径 | 作用 | 应有保护 |
| --- | --- | --- |
| GET/PUT /api/config | 读取和更新筛选、推送、调度和每日摘要配置 | Basic Auth |
| POST /api/enroll/toggle | 开关自动选课 | Basic Auth |
| POST /api/trigger | 触发 full/quick 抓取，支持后台或等待结果 | Basic Auth |
| GET /api/logs/push、GET /api/logs/enroll | 推送和选课日志 | Basic Auth |
| POST /api/manual-push | 管理员手动推送 | Basic Auth |
| GET /api/subscribers | 订阅用户、活跃度、暂停和发送统计 | Basic Auth |
| POST /api/admin/broadcast/service-update | 发送服务更新 | Basic Auth |
| POST /api/admin/subscriber/<id>/toggle-active | 切换订阅状态 | Basic Auth |
| POST /api/admin/subscriber/<id>/clear-pause | 清除暂停 | Basic Auth |
| POST /api/admin/subscriber/<id>/pause-push | 管理员暂停用户推送 | Basic Auth |
| POST /api/cleanup-expired | 清理过期课程 | 当前 Nginx 未显式保护，需修复 |
| POST /api/test-email | 测试邮件发送 | 当前 Nginx 未显式保护，需修复 |

### 7.6 二维码 API

| 方法与路径 | 作用 |
| --- | --- |
| GET /api/qrcode/context | 当前用户、课程上下文、贡献统计、奖励阈值和排行榜 |
| GET /api/qrcode/uploads | 获取公开二维码列表 |
| POST /api/qrcode/uploads | multipart 上传二维码，可关联 course_id 和备注 |
| GET /qrcode/uploads/<path> | 读取上传图片 |

## 8. 数据模型与数据生命周期

数据库文件为根目录 boya_agent.db，SQLAlchemy 使用 SQLite，连接配置包括 check_same_thread=false、30 秒超时、WAL、NORMAL 同步和 busy_timeout。init_db 会 create_all 并执行增量字段迁移，commit_with_retry 处理 database is locked。

| 表 | 主要用途 | 关键字段/关系 |
| --- | --- | --- |
| courses | 课程主数据和抓取快照 | name、时间、地点、校区、容量、报名窗口、签到、status、expired、first_seen、last_seen、pushed |
| filter_config | 全局筛选和调度配置 | categories、whitelist、blacklist、priority_keywords、self_sign_only、auto_enroll、email/rss/telegram、摘要和 interval |
| push_logs | 传统课程推送日志 | course_id、push_type、sent_at、success、message |
| enroll_logs | 自动选课日志 | course_id、课程名、时间、success、message |
| email_subscribers | 用户订阅和偏好 | email、token、verified、active、categories、campus、self_sign_only、pause、门户活跃时间、验证码 |
| login_bridge_tickets | 跨设备登录桥 | ticket、subscriber_id、email、expires_at、claimed_at |
| course_reminders | 用户课程提醒 | subscriber_id、course_id、提前分钟数、sent |
| notification_events | 用户通知时间线和去重依据 | subscriber_id、course_id、event_type、delivery_mode、channel、success、sent_at |
| qrcode_uploads | 二维码贡献记录 | course_id、贡献者、文件路径、原名、mime、size、verification_status、is_active、时间 |

### 8.1 重要数据关系

- email_subscribers 一对多关联 course_reminders。
- email_subscribers 一对多关联 notification_events。
- courses 一对多关联 push_logs、enroll_logs、course_reminders、notification_events 和 qrcode_uploads。
- login_bridge_tickets 关联订阅者并有独立过期时间。
- qrcode_uploads 目前通过字符串保存贡献者邮箱，同时可选关联课程 ID。

### 8.2 隐私和安全相关数据

系统会保存订阅邮箱、登录 token、验证码、桥接票据、推送偏好、门户活跃时间和通知记录。代码中没有发现真实账号密码提交到仓库，但生产环境应重点保护 boya_agent.db、config/.env、上传目录和日志文件。

## 9. 前端与交互结构

| 前端文件 | 主要职责 |
| --- | --- |
| web/templates/home.html、web/static/home.js、home.css | 首页介绍、实时洞察卡片和入口 |
| web/templates/subscribe.html、persistent_login.js、subscribe_bridge.js | 订阅、验证码、已注册用户入口和跨设备桥接 |
| web/templates/portal.html、portal.js、portal.css | 用户门户、课程卡片、通知、提醒、偏好和移动端适配 |
| web/templates/index.html、app.js、style.css | 管理后台的课程、配置、自动选课、订阅者和日志标签页 |
| web/templates/qrcode.html、qrcode.js、qrcode.css | 二维码大厅、课程上下文、上传预览、列表和排行榜 |

前端对非 JSON 响应做了一定容错，以适配 Nginx 错误页或后台任务响应。portal.js 仍有乱码字符串和较大的单文件逻辑，后续适合拆分为会话、课程、通知、提醒和偏好模块。

## 10. 部署和运维

### 10.1 目标部署拓扑

客户端 → Nginx → 127.0.0.1:5000 Flask → src/main.py → SQLite/Playwright/SMTP/Telegram。

当前配置文件中的公共域名是 buaaboya.top，服务端监听 127.0.0.1:5000；Nginx 设置了 10 MB 请求体上限、15 秒连接超时和 240 秒读写超时，适配较慢的 Playwright 触发请求。

### 10.2 systemd

deploy/boya-agent.service 配置了：

- 服务名 boya-agent；
- WorkingDirectory /home/boya-agent；
- 使用虚拟环境 Python 执行 src/main.py；
- EnvironmentFile 指向 config/.env；
- Restart=always，重启间隔 10 秒；
- 当前 User=root。

以 root 运行应用会放大 Web、依赖、上传文件和浏览器进程被利用后的影响范围，建议使用专用低权限用户，并单独授予日志、数据库和上传目录权限。

### 10.3 Nginx 鉴权边界

显式 Basic Auth 保护了 /admin、/api/config、/api/enroll/toggle、/api/logs、/api/manual-push、/api/trigger、/api/subscribers 和 /api/admin 等路径。公开开放首页、订阅、门户资源、课程查询、RSS、二维码和登录验证等路径符合产品设计。

但 Nginx 文件只有 listen 80 的 server 块，没有看到 443 TLS server、HTTP 到 HTTPS 重定向或证书配置。文档虽然按 HTTPS 访问描述，实际部署是否由外部负载均衡终止 TLS 需要确认；如果没有外部 TLS，邮箱链接和 Secure Cookie 的预期会失效。

此外，Nginx 的 auth_basic 兜底是 off，/api/cleanup-expired 和 /api/test-email 未被显式匹配，会落入公开兜底路由。必须在 Nginx 或应用层补充保护。

### 10.4 日志、数据库和浏览器

- Loguru 控制台加文件日志，文件按天轮转，保留 30 天。
- SQLite 使用 WAL 和锁等待重试，适合单进程轻量部署，不适合作为高并发多实例数据库。
- 浏览器默认 headless，带 no-sandbox 和 no-dev-shm 参数，适合容器/服务器环境但需要评估运行用户和浏览器安全边界。
- scheduler.py 会在达到浏览器运行次数后回收；如果存在热门课程，会延迟回收到硬上限，优先保证监控连续性。

## 11. 配置基线

| 配置 | 当前示例/默认值 | 作用 |
| --- | --- | --- |
| WEB_HOST | 127.0.0.1 | Flask 监听地址 |
| WEB_PORT | 5000 | Flask 端口 |
| APP_PUBLIC_BASE_URL | https://buaaboya.top | 邮件和桥接链接的公共地址 |
| SCRAPE_INTERVAL_MINUTES | 10 | 普通 full 抓取间隔 |
| PUSH_URGENT_DIGEST_MINUTES | 5 | 紧急缓冲刷新间隔 |
| PUSH_SOON_DIGEST_MINUTES | 30 | 近期缓冲刷新间隔 |
| HOT_COURSE_REMAINING_THRESHOLD | 3 | 热门课程剩余名额阈值 |
| HOT_COURSE_FILL_RATIO | 0.82 | 热门课程填充率阈值 |
| ACTIVE_ENROLL_SCRAPE_SECONDS | 30 | 活跃报名窗口 quick 监控间隔 |
| HOT_COURSE_WATCH_SECONDS | 15 | 热门课程 quick 监控间隔 |
| SMTP_* | 由 .env 配置 | 验证邮件、通知邮件和回退通道 |
| SMTP_PROXY | http://127.0.0.1:7890 | SMTP 代理，可选 |
| WEB_SECRET_KEY | 必须生产环境显式设置 | Flask 会话密钥 |

config/default_config.json 的业务默认值包括：仅自主签到、最少剩余 1 个、RSS 开启、邮件和 Telegram 用户广播关闭、每日摘要关闭、自动选课关闭。配置接口可以动态修改全局 FilterConfig，调度间隔变化会同步到调度器。

## 12. 测试和当前验证结果

### 12.1 已有测试覆盖

tests/test_qrcode_feature.py 覆盖课程上下文、课程关联上传/列表和按课程及周期统计排行榜。

tests/test_scraper_scheduler_regressions.py 覆盖并行开课、时间漂移和去重、网络 payload 归一化、稀疏快照健康检查、表头识别、生命周期过期、活跃/热门监控、课程状态输出、浏览器回收、触发任务去重、超时关闭浏览器、预告行解析、空页重试、DOM 快路径和回退解析等回归场景。

### 12.2 本次静态验证

- JavaScript 语法检查：app.js、portal.js、qrcode.js、persistent_login.js、subscribe_bridge.js 均通过 node --check。
- Python 文件已纳入检查范围，但当前环境未安装 SQLAlchemy，pytest 在收集阶段因 ModuleNotFoundError: No module named sqlalchemy 停止，不能据此判断测试通过或失败。
- 仓库工作区在整理前为干净状态，当前新增本文件是本次文档产物。
- boya_agent.db 为 0 字节空文件，未进行初始化或写入，避免改变现有运行数据。

建议在安装 requirements.txt 后执行：

1. python -m pytest -q
2. 对 src、web、tests 下所有 Python 文件执行 py_compile。
3. 启动本地服务后逐项验证订阅、验证、门户、暂停、提醒、二维码上传和管理鉴权。
4. 通过 Nginx 做一次真实的保护边界测试，尤其是 cleanup-expired 和 test-email。

## 13. 风险登记与优先级

| 优先级 | 风险 | 证据 | 建议 |
| --- | --- | --- | --- |
| P0 | 已注册邮箱可直接换取门户会话 | web/app.py 的 /api/login/request 查询 verified+active 后直接返回 token/cookie；send_login_email 没有被该流程调用 | 改为发送一次性登录链接或验证码；服务端只在验证邮箱所有权后签发 Cookie；增加速率限制和审计 |
| P0 | 清理数据和测试邮件接口可能公开 | Nginx 兜底 auth_basic off，未见 /api/cleanup-expired、/api/test-email 专门保护 | 显式加入 Basic Auth 或应用层管理员鉴权；限制 test-email 目标和频率 |
| P0 | 二维码公开列表可能泄露贡献者邮箱 | qrcode_uploads.to_dict 返回原始 contributor_email，公开列表直接返回该字典；列表按 is_active 而非 verification_status 过滤 | 公开响应移除原始邮箱；仅展示掩码；审核通过后公开；增加文件内容校验、大小限制、过期和去重 |
| P1 | HTTPS 配置与文档不一致 | deploy/nginx_boya.conf 当前只看到 listen 80；应用按请求协议决定 Secure Cookie | 补充 TLS/重定向或明确外部 TLS 终止；验证邮件链接、Cookie 和 WebVPN 回调 |
| P1 | 管理安全依赖 Nginx，直连 Flask 无鉴权 | web/app.py 未见管理员装饰器，配置、抓取和订阅者操作由 Nginx 保护 | 应用层增加管理员认证/签名，Nginx 作为第二层；限制 CORS 和管理接口来源 |
| P1 | CORS 全开放且没有 CSRF 防护 | web/app.py 使用 CORS(app)，多个 POST 会改变订阅、配置或数据 | 限制允许来源，使用 CSRF token 或 SameSite/Origin 校验；区分公共和管理 API |
| P1 | Flask secret key 有硬编码回退值 | app.secret_key 使用 boya-agent-secret-key 作为默认值 | 生产启动时强制检查 WEB_SECRET_KEY，缺失直接失败 |
| P1 | systemd 以 root 运行 | deploy/boya-agent.service 的 User=root | 使用专用低权限用户，拆分目录权限和上传目录权限 |
| P1 | 浏览器会话并非跨重启持久化 | BROWSER_DATA_DIR 未使用，storage_state=None | 如果确有需求，实现加密/受限的持久化存储并定义会话清理策略 |
| P1 | Atom 生成器存在未定义变量 | src/push/rss_feed.py 的 generate_atom_feed 在循环中使用未定义 check_in_label | 复用课程对象的签到标签计算逻辑，并补充有数据的 Atom 测试 |
| P1 | 推送开关的实际语义不一致 | scheduler.py 的 _do_push 只依据 email_enabled；telegram_enabled/rss_enabled 不控制常规发送/公开源 | 明确配置命名，按 channel 真正控制发送，或删除无效开关 |
| P1 | 用户筛选和邮件筛选的签到判定不一致 | filters.py/course_state.py 综合多个字段，email_push.py 主要检查 check_in_method | 抽取单一 is_self_check_in 函数供门户、过滤器和邮件共用 |
| P2 | 抓取间隔、摘要窗口和历史文档不一致 | README、CONTEXT、now、.env.example、scheduler.py 的描述和默认值不同 | 以一份版本化运行配置为准，自动生成或同步运维文档 |
| P2 | 前端编码和维护债务 | portal.js 存在乱码，邮件/Telegram 文件包含历史代码痕迹，门户脚本较大 | 统一 UTF-8、移除死代码、拆分模块并增加 lint/build 检查 |
| P2 | 运行可观测性不足 | 现有日志和状态计数较完整，但缺少分阶段耗时、抓取延迟分布和持久化健康历史 | 增加抓取阶段计时、推送失败原因分类、告警指标和保留策略 |
| P2 | 自动选课有账号和封禁风险 | roadmap 已注明 SSO、代理池和 IP 封禁风险 | 继续默认关闭；增加显式二次确认、每日上限、审计和熔断 |

## 14. 建议实施顺序

### P0：先修上线安全

1. 重构 /api/login/request，取消“知道邮箱即可登录”的路径。
2. 保护 cleanup-expired、test-email 和所有变更数据的管理接口。
3. 二维码公开数据脱敏，增加审核状态、上传内容验证和访问控制。
4. 确认 HTTPS 终止位置，确保 Secure Cookie 和邮件链接真实可用。

### P1：统一行为和降低运维风险

1. 抽取统一的签到/可报名/过期状态判断。
2. 统一抓取间隔、摘要窗口和推送开关的命名与文档。
3. 修复 Atom 端点并增加 RSS/Atom 集成测试。
4. 限制 CORS，增加 CSRF/Origin 防护，移除硬编码密钥回退。
5. 将 systemd 服务切换到专用用户，明确数据库、日志和上传目录权限。
6. 决定是否需要跨重启浏览器会话持久化，并按安全要求实施。

### P2：改善产品和工程质量

1. 清理前端乱码、重复/历史代码和大文件模块。
2. 补充通知中心、订阅用户和抓取阶段的运营指标。
3. 完善二维码贡献激励、过期、举报、去重和审核后台。
4. 在自动选课之前完成演练环境、熔断和全量审计。

## 15. 关键文件索引

| 文件 | 说明 |
| --- | --- |
| README.md | 项目定位、启动方式和目录说明 |
| CONTEXT.md | 早期系统上下文、数据流和旧部署信息 |
| now.md | 当前阶段记录、风险和后续方向 |
| BUSINESS_ROADMAP.md | 业务路线图和长期方向 |
| change-3-07.md | 登录、推送和订阅体验迭代记录 |
| src/main.py | 进程入口 |
| src/auth.py | WebVPN/SSO 登录 |
| src/scraper.py | 抓取、解析、去重、健康检查和保存 |
| src/scheduler.py | 调度、监控、推送和生命周期 |
| src/models.py | 数据库模型和迁移 |
| src/course_state.py | 课程状态语义 |
| src/filters.py | 全局筛选和自动选课候选 |
| src/push/email_push.py | 邮件通知、去重和订阅者筛选 |
| src/push/telegram_bot.py | Telegram 辅助通知 |
| src/push/rss_feed.py | RSS/Atom |
| src/qrcode_service.py | 二维码存储、贡献统计和排行榜 |
| web/app.py | Flask 页面、会话、订阅、门户和管理 API |
| web/qrcode_feature.py | 二维码页面和 API |
| web/static/portal.js | 用户门户前端 |
| web/static/app.js | 管理后台前端 |
| config/.env.example | 环境变量模板 |
| config/default_config.json | 业务默认配置 |
| deploy/nginx_boya.conf | Nginx 反向代理和鉴权边界 |
| deploy/admin_access.md | 管理后台部署访问说明 |
| deploy/boya-agent.service | systemd 服务 |
| tests/test_qrcode_feature.py | 二维码测试 |
| tests/test_scraper_scheduler_regressions.py | 抓取和调度回归测试 |

## 16. 总结

项目已经从单纯的抓取脚本演进为包含课程采集、状态判断、订阅推送、用户门户、管理后台和二维码共享的轻量服务。核心抓取链路具备较好的异常快照防护、去重、生命周期和多级监控设计，产品基础是完整的。

当前最重要的工程判断是：功能面已经足够支撑一次系统化加固，下一阶段应优先收敛认证、管理接口、二维码隐私、TLS、配置语义和状态判定的一致性，再继续增加运营或自动选课能力。
