# 2026-09-24 生产运行核对

## 核对范围

本记录只描述 2026-09-24（北京时间）对当前腾讯云生产实例的只读核对结果，不执行服务重启、配置修改、数据库写入、文件删除或通知发送。

- 生产入口：`https://buaaboya.top`
- 应用目录：`/home/boya-agent`
- 服务：`boya-agent`
- 生产提交：`d9f3fda52b8eae27097300ee0e8ba71bfd00cca5`
- 诊断来源：[Server Diagnostics #35903500799](https://github.com/2725238326/boya-agent/actions/runs/35903500799)
- 本地仓库当前提交：`7425f54d651f5743daa10cedddfc571edd07d90a`

本地仓库包含生产提交之后的文档提交；这些文档提交没有改变服务器运行代码，也没有触发生产重启。后续发布报告必须分别记录“仓库当前提交”和“服务器运行提交”。

## 线上结果

### 服务和代码

- 服务器工作树：clean。
- `boya-agent`：active、enabled。
- 服务最近一次启动：2026-09-20 16:44:46 CST。
- SQLite `PRAGMA integrity_check`：`ok`。
- 当前依赖任务表存在，通知任务状态可读。

### 业务配置

生产数据库 `filter_config` 当前值为：

| 字段 | 值 |
| --- | ---: |
| `interval_minutes` | `10` |
| `email_enabled` | `0` |
| `telegram_enabled` | `0` |
| `daily_summary_enabled` | `0` |
| `auto_enroll_enabled` | `0` |
| `rss_enabled` | `1` |

结论：邮件、Telegram、每日摘要和自动选课均关闭；RSS 公开源开启。

### 抓取和数据

- 数据库课程记录数：`49`。
- 最近一次 `last_seen`：`2026-09-24 02:35:27`（北京时间）。
- 诊断窗口内 quick 轮持续完成；一次典型轮次解析 20 条、去重后 11 条，跳过 9 门已结束课程，更新 11 条已有记录。
- 未发现诊断窗口内的服务崩溃、持续失败或异常空快照证据。

### 通知任务

当前 `notification_jobs` 汇总为：

```text
succeeded / email = 6079
```

未发现 pending、processing 或 failed 任务。

### 资源

- 根文件系统：40 GiB 总量，约 21 GiB 已用，约 17 GiB 可用，使用率 55%。
- 内存：总量约 1.9 GiB；free 约 108 MiB；available 约 811 MiB。
- Swap：2.0 GiB，总使用约 739 MiB，剩余约 1.3 GiB。
- `boya-agent` cgroup 当前内存约 569 MB，上限约 1.3 GiB。

资源结论：磁盘和 cgroup 内存上限目前没有达到立即处置条件；Swap 已有明显使用量，列入持续观察项。未经新的证据，不直接删除日志、清理运行目录或重启服务。

### 公开入口

本地只读探测结果：

| 地址 | 结果 |
| --- | --- |
| `/healthz` | HTTP 200，`status=ok` |
| `/api/courses` | HTTP 200，返回 5 门公开课程 |
| `/` | HTTP 200 |
| `/QRcode` | HTTP 200 |
| 未授权 `/api/status` | HTTP 401 |

## 后续动作

1. 将本记录作为当前生产运行事实来源，后续发布记录链接回本文件。
2. 继续观察 available memory、Swap、driver 数量、抓取连续失败和周更备份。
3. 生产代码变更前先完成本地预发布验证；当前仓库 `7425f54` 文档提交不自动部署。
4. 任何生产清理操作都必须先完成文件级清单、备份和回滚验证。
