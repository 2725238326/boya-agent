# 推送机器人渠道调研（Telegram 更新 + 飞书接入）

整理时间：2026-09-20。目的：盘点 Telegram Bot API 近一年值得采用的新能力，并把飞书机器人的两种接入形态和本项目落地路径讲清楚。本文是调研笔记，落地时再补正式实现文档。

## 一、Telegram Bot API 近况

当前最新版 **Bot API 10.3**（2026-08-24）。本项目锁定 `python-telegram-bot==22.8`，覆盖到 9.x 系列能力；现有代码只用了 `sendMessage` + MarkdownV2/HTML + URL 按钮，不升级也能继续用。

近一年与本项目相关的更新（按可用价值排序）：

| 能力 | 版本 | 对本项目的价值 |
| --- | --- | --- |
| 彩色/图标 inline 按钮（`style`、`icon_custom_emoji_id`） | 9.4 (2026-02) | "查看详情/去选课"按钮可以按语义着色（绿=可报名、红=已满），比现在清一色蓝更直观 |
| Rich Messages（`sendRichMessage` + block 实体：表格、可折叠引用、详情块） | 10.x (2026-05~) | 每日汇总可从纯文本行升级成**真正的表格**；长课程列表可折叠 |
| Ephemeral Messages（群内仅指定用户可见） | 10.x | 群内发"仅你可见"的签到二维码/提醒，不用拉私聊 |
| DisabledButton（`InlineKeyboardButton.disabled`） | 10.3 | 课程截止后按钮可置灰而非消失，状态更明确 |
| 私聊话题（has_topics_enabled）、Checklist、礼物经济 | 9.3-9.5 | 与本项目场景关系不大，知道即可 |

注意：`sendRichMessage`/Ephemeral 属于 10.x，需要 python-telegram-bot 升级到对应版本（本项目锁 22.8，覆盖至 9.x）。升级前核对 `constraints.txt` 并跑一次推送回归。

**当前代码债**：`enroll_{id}` callback 按钮已移除——callback_data 需要 polling 或 webhook 运行时处理 `callback_query`，本项目从未部署该运行时，按钮按下只会永远转圈。如果未来要做"在 Telegram 里一键选课"，需要先起 bot 运行时（`Application` + polling/webhook），工作量在"进程常驻 + 回调幂等"上，不在按钮本身。

## 二、飞书机器人：两种形态

飞书的"机器人"不是一个东西，接入成本和上限差别很大，先分清：

| 维度 | 自定义机器人（群 webhook） | 自建应用机器人 |
| --- | --- | --- |
| 本质 | 一个群内的只写 webhook 地址 | 开放平台上的一个应用实体 |
| 审核 | **不需要**，群管理员添加即用 | 需要租户管理员审核（企业内）/发布 |
| 能力 | 只能往所在群发消息，**无任何数据访问权限**，不能收消息、不能私聊 | 发消息（群/私聊）、收事件、卡片交互、通讯录/日历等全 OpenAPI |
| 目标标识 | webhook URL 自带群身份 | `receive_id`（`chat_id`/`open_id`/`email`） |
| 限频 | 单机器人 100 次/分、5 次/秒；正文 ≤20KB | 发消息 1000 次/分、50 次/秒 |
| 接入成本 | 一次 HTTP POST | 建应用、配权限、管 token，约半天到一天 |
| 适合 | 监控报警、运营推送、**本项目管理员告警/课程群推** | 双向交互、按用户私聊、审批/卡片流 |

### 2.1 自定义机器人（webhook）

接入三步：群设置 → 群机器人 → 添加"自定义机器人" → 拿 `https://open.feishu.cn/open-apis/bot/v2/hook/<token>`。

发消息就是 POST JSON：

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"msg_type":"text","content":{"text":"request example"}}' \
  https://open.feishu.cn/open-apis/bot/v2/hook/<token>
```

成功返回 `{"code":0,"msg":"success",...}`。消息类型支持 `text`、`post`（富文本）、`interactive`（消息卡片，可带按钮）、`image` 等。

**安全设置（强烈建议至少开一种）**：
- 自定义关键词：消息必须含至少一个关键词（最多 10 个）——防 webhook 泄露被刷；
- IP 白名单：只放行指定来源 IP/段；
- 签名校验：`HmacSHA256(timestamp + "\n" + secret)` 的 Base64，时间戳 1 小时内有效。Python 实现：

```python
import base64, hashlib, hmac, time

def feishu_sign(secret: str) -> dict:
    ts = str(int(time.time()))
    sign = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
    ).decode()
    return {"timestamp": ts, "sign": sign}  # 并入请求体顶层
```

注意：签名的 HMAC 消息体是 `timestamp\nsecret`，key 用空字节（`hmac.new` 第一个参数传 `b""` 或如上直接把内容当 key 输入的官方示例写法——以官方文档代码为准，实测一次即可确认）。限频建议避开整点/半点（官方明确提示此时段易触发 11232 限流）。

### 2.2 自建应用机器人

流程：开发者后台建企业自建应用 → 开启机器人能力 → 拿 `app_id`/`app_secret` → 申请权限（发消息至少 `im:message` 或 `im:message:send_as_bot`）→ 设置可用性范围 → 版本发布/管理员审核。

发消息走 OpenAPI（SDK 自动管 `tenant_access_token`）：

```python
import lark_oapi as lark
from lark_oapi.api.im.v1 import *

client = lark.Client.builder().app_id("cli_xxx").app_secret("xxx").build()
req = CreateMessageRequest.builder() \
    .receive_id_type("chat_id") \
    .request_body(CreateMessageRequestBody.builder()
        .receive_id("oc_xxx")           # 群 chat_id；私聊用 open_id
        .msg_type("interactive")        # 或 text/post
        .content('{"type":"template","data":{...}}')
        .build()) \
    .build()
resp = client.im.v1.message.create(req)
```

**收事件有两条路**：
- 传统 webhook：给开放平台一个公网 HTTPS 地址，事件 POST 过来，需处理验签/解密/url_verification 握手；
- **长连接（推荐上手用）**：`lark_oapi` SDK 内置 WebSocket 客户端，本地开发不用公网 IP/内网穿透，5 分钟建连；事件 3 秒内要处理完（否则重推）；每应用最多 50 连接、集群模式随机投递。

**卡片交互**：卡片按钮回调走 `card.action.trigger` 事件；卡片可以用官方"卡片搭建工具"可视化拖拽生成 JSON。

### 2.3 数量与成本

**能建几个**：
- 自定义机器人：**每个群最多 15 个机器人**（含应用机器人；chat-member API 也确认群内容纳 ≤15 个）。机器人绑定单群、不跨群，群数本身不限——按"群 × 15"理解即可。
- 自建应用：按企业版本有数量上限（超限报 `k_app_ec_400001940`），免费版量级对单项目用途完全够；**测试技巧**：可以自行创建一个新企业（免费），在新企业里加权限免审核，适合练手阶段。

**钱从哪来**：
- 自定义机器人 webhook：**完全免费**，不占 OpenAPI 月度配额，成本约束只有频率（100 次/分、5 次/秒、20KB/条）——告警/群推场景零成本。
- 自建应用：创建免费，成本主要是**月度 API 调用配额**。基础免费版单租户全部自建应用共享月度上限（官方 2026 年限时调到 100 万次/月，常态值为万次级，以"费用中心 > 权益数据"页为准），超量后调用失败返回 `429`/`99991403`；付费版（商业版/企业版）**不限量**。另有少数高频控等级接口付费版上限更高（如某档 50/s → 100/s）。部分 OpenAPI 标注"不计入收费用量"，查 API 文档"计入收费用量"列可确认。
- 对本项目量级（告警几条/天 + 群推几十条/天），免费配额绰绰有余；瓶颈永远先撞在频率上而不是额度上。

### 2.4 学习路径建议（从零到会做）

1. 自定义机器人 webhook（半小时）：建群 → 加机器人 → curl 发 text → 换 post 富文本 → 换 interactive 卡片 → 打开签名校验。到这里"推送型"就全会了。
2. 自建应用（半天）：建应用 → SDK 发消息到群（chat_id 从群信息或事件里拿）→ 用长连接收 `im.message.receive_v1` 回显用户消息 → 加一张带按钮的卡片处理 `card.action.trigger`。到这里双向交互就全会了。
3. 进阶按需：scope 权限体系、`tenant_access_token` 生命周期（SDK 已封装）、应用上架审核、国际化（feishu.cn 国内版 / larksuite.com 海外版域名不通用）。

## 三、对本项目的落地建议

现实排序：

1. **自定义机器人做管理员告警通道**（成本最低、收益立刻可见）：scrape 失败告警、每日汇总副本推到运维群。一个 `aiohttp` POST + 签名，约 50 行，新环境变量 `FEISHU_WEBHOOK_URL` + `FEISHU_WEBHOOK_SECRET`。可与现有 `send_status_message` 并列成一个"管理员通知"抽象。
2. **课程群推**：如果博雅用户有飞书群，新课程/捡漏通知用 interactive 卡片（含"打开课程页"按钮）比纯文本效果好。
3. **按用户私聊**（重投入，先不做）：需要自建应用 + 用户 open_id 绑定流程（用户在飞书里给机器人发消息 → 拿 open_id → 与门户账号关联），订阅表要加渠道绑定字段。等确有用户要求再做。

暂缓项：Ephemeral/富文本表格值得在下次升级 python-telegram-bot 时一起评估；飞书"自定义机器人不能收消息"，所以双向交互（比如在群里回"订阅"）只能走自建应用路线。

## 四、参考

- Telegram Bot API changelog: https://core.telegram.org/bots/api-changelog
- 自定义机器人指南: https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/bot-v3/use-custom-bots-in-a-group
- Python SDK: https://github.com/larksuite/oapi-sdk-python（`lark-oapi`）
- 发消息 API: `POST /open-apis/im/v1/messages`（限频 1000/min、50/s）
- 长连接事件: 飞书文档"处理事件"章节（SDK `EventDispatcherHandler` + `ws.Client`）
