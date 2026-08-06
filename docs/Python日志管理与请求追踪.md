# Python 日志管理与请求追踪

## 1. Python 中与 Winston 对应的选择

Node.js 的 Winston 同时提供级别、格式化和 Transport。Python 标准库 `logging` 对应同一组能力：

| Winston | Python logging | 本项目用途 |
| --- | --- | --- |
| logger | `logging.getLogger(name)` | 模块或业务日志入口 |
| level | `DEBUG/INFO/WARNING/ERROR/CRITICAL` | 控制输出详细程度 |
| format | `Formatter` | 文本或 JSON 格式 |
| transport | `Handler` | stdout、文件或远程输出 |
| metadata | `extra={...}` | request_id、状态码、耗时等字段 |

Loguru 更容易上手，structlog 更擅长结构化上下文，但 FastAPI、Uvicorn 和大量第三方库原生使用 `logging`。当前项目先用标准库，避免出现两套日志系统。以后接入 structlog 也可以继续兼容标准库日志。

## 2. 当前实现解决什么问题

每个 HTTP 请求进入应用时生成一个服务端 `request_id`。它同时出现在响应 Header、API 响应 `meta.requestId`、访问日志和未知异常日志中：

```text
浏览器请求
  -> FastAPI 中间件生成 request_id 并绑定到 ContextVar
  -> Router -> Service -> SQLAlchemy（期间 logger 输出会自动带 request_id）
  -> 中间件记录 method/path/status_code/duration_ms
  -> 响应写入 X-Request-ID
  -> finally 清理 ContextVar，防止污染下一次请求
```

线上用户反馈问题时，只需提供响应中的 `X-Request-ID`。运维人员在日志平台按该字段搜索，就能把访问日志和异常调用栈关联起来。

## 3. 配置项

| 环境变量 | 默认值 | 作用与影响 |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | `DEBUG` 更详细；`WARNING` 会隐藏正常访问日志 |
| `LOG_FORMAT` | `text` | 本地使用 `text`，日志平台使用 `json` |
| `LOG_TO_FILE` | `false` | `false` 写 stdout；`true` 写轮转文件 |
| `LOG_DIRECTORY` | `logs` | 文件模式根目录，其下创建 app 和 error 两个通道 |
| `LOG_MAX_BYTES` | `20971520` | 单个日期文件达到该大小后进行大小轮转 |
| `LOG_FILES_PER_DAY` | `5` | 单通道单日包含当前文件在内的文件数上限 |
| `LOG_RETENTION_DAYS` | `14` | 按 UTC 日期保留的天数 |

配置优先级仍为“系统环境变量 > `.env.<环境>` > `.env` > Settings 默认值”。修改后必须重启应用进程。

## 4. 开发环境查看日志

### 4.1 本地与生产环境对照

| 维度 | 本地开发/调试 | 生产环境 |
| --- | --- | --- |
| 主要目标 | 快速看懂代码执行过程和参数错误 | 稳定排障、审计趋势、告警和容量控制 |
| 默认等级 | `DEBUG` 或 `INFO`，按排错需要临时调整 | 通常 `INFO`；高流量接口可提高到 `WARNING` 或采样 |
| 格式 | 人容易阅读的单行文本 | 单行 JSON，字段稳定并便于机器检索 |
| 输出位置 | 终端；必要时短期写本地文件 | 容器 stdout，由采集器异步发送到日志平台 |
| 保存期限 | 通常不长期保存，重启或手动清理即可 | 按日志类型分层保存，例如应用日志 14～30 天、安全审计 90～180 天 |
| 调用栈 | 调试异常时完整保留 | `ERROR` 保留完整调用栈，但不能返回客户端 |
| SQL 日志 | 仅定位查询问题时临时打开 | 默认关闭参数和完整 SQL；使用慢查询日志或采样 |
| 敏感数据 | 同样禁止记录真实密码和 Token | 必须脱敏，并通过平台权限控制、加密和审计访问 |

企业应用不会维护两套业务代码，而是让同一套日志调用通过环境配置改变等级、格式和输出目标。本项目的 `.env.development` 使用 `text + stdout`，`.env.production` 使用 `json + stdout`。

### 4.2 本地调试方式

```powershell
$env:ENV = "development"
uv run fastapi dev app/main.py
```

默认输出适合阅读的文本。需要临时验证 JSON：

```powershell
$env:LOG_FORMAT = "json"
uv run fastapi dev app/main.py
```

业务代码使用模块 Logger，不要使用 `print()`：

```python
import logging

logger = logging.getLogger(__name__)
logger.info("Post published", extra={"post_id": post.id})
```

不要记录密码、Bearer Token、Cookie、完整数据库 URI、`SECRET_KEY`、上传文件正文或用户隐私。当前访问日志有意只记录 URL path，不记录 query string 和 request body。

调试完成后应把临时 `logger.debug()`、SQL echo 和第三方库 DEBUG 日志关闭。它们即使不含错误，也会显著增加 I/O、降低可读性，并可能把参数内容带入日志。

## 5. 生产环境推荐架构

容器内部推荐只写 stdout JSON，不直接从 FastAPI 调用第三方日志“新增接口”：

```text
FastAPI stdout(JSON)
  -> Docker logging driver / Promtail / Fluent Bit / Vector
  -> Loki / Elasticsearch / 云日志服务
  -> Grafana / Kibana 查询、告警和权限控制
```

原因是远程日志平台故障时，业务请求不应等待日志 HTTP 接口；采集器具有缓冲、批量、重试和背压能力。日志平台中的“增删改查”通常不是业务 CRUD：新增由采集器写入，查询由检索语法完成，删除由 retention policy 管理，历史日志通常不允许修改。

以 Loki 为例，可以按 JSON 字段查询某个请求：

```logql
{app="fastapi-blog"} | json | request_id="用户提供的请求ID"
```

生产环境的 `.env.production` 已配置 `LOG_FORMAT=json`、`LOG_TO_FILE=false`。Docker Compose 可为容器 stdout 设置轮转，Loki/Elasticsearch 还应单独配置服务端保留策略；`LOG_RETENTION_DAYS` 只管理本应用自己写出的文件，不能删除第三方平台中的数据。

## 6. 单机文件日志与自动清理

只有没有 Docker 日志采集器的单机部署才建议启用：

```dotenv
LOG_TO_FILE=true
LOG_DIRECTORY=logs
LOG_MAX_BYTES=20971520
LOG_FILES_PER_DAY=5
LOG_RETENTION_DAYS=30
```

应用把完整时间线写入 `logs/app/YYYY-MM-DD.log`，并把 ERROR/CRITICAL 额外复制到 `logs/error/YYYY-MM-DD.log`。同一天达到 `LOG_MAX_BYTES` 后追加 `.1` 等大小轮转文件，`LOG_FILES_PER_DAY` 限制单通道单日总文件数。日期切换后的首次写入会删除保留窗口外的历史文件。多 Worker 仍可能竞争本地轮转文件，因此容器部署应改用 stdout 加外部采集器。

## 7. 企业应用如何维护日志等级和内容

等级表示事件严重程度，而不是开发人员主观认为“这条信息重要”。推荐约定：

| 等级 | 应记录的内容 | 不应记录的内容 |
| --- | --- | --- |
| `DEBUG` | 分支选择、缓存命中、经过脱敏的调试上下文 | 密码、Token、完整请求体；生产常规流水 |
| `INFO` | 请求完成、启动/停止、登录成功、帖子发布等关键状态变化 | 循环中每一项、重复的大对象、全部 ORM 实体 |
| `WARNING` | 可恢复的刷新失败、重试、限流、降级和即将耗尽的资源 | 已完全正常处理且无需关注的用户输入错误 |
| `ERROR` | 请求失败、数据库或第三方依赖异常，并附调用栈 | 用于表达普通 `404/422` 或用户输错密码 |
| `CRITICAL` | 关键配置错误、核心存储全面不可用、进程无法继续服务 | 能由单次重试恢复的普通异常 |

日志消息使用稳定的事件描述，例如 `Post published`，变化的数据放在结构化字段中：

```python
logger.info(
    "Post published",
    extra={"post_id": post.id, "user_id": current_user.id},
)
```

不要把完整对象拼进 message。字段名应在团队内统一，例如始终使用 `user_id`，不要同时出现 `uid`、`userId` 和 `member_id`。建议在代码评审中检查：等级是否正确、是否包含排错所需 ID、是否泄露数据、是否在高频循环中输出。

访问日志、业务日志和安全审计日志的用途不同：

- 访问日志记录 method、path、status_code、duration_ms 和 request_id。
- 业务日志记录发布、上下架、删除等关键状态变化及资源 ID。
- 安全审计记录谁在何时执行了管理员操作，通常保留更久且限制删除权限。

当前项目已实现前两类的基础能力，但还没有不可篡改的独立安全审计存储，不能把普通应用日志当成完整合规审计系统。

## 8. 自动删除、防止日志无限增长

日志容量必须在每一层分别设上限，只设置应用的 `LOG_RETENTION_DAYS` 并不能限制 Docker 或第三方平台。

### 8.1 第一层：应用或 Docker 节点

文件模式由本项目每天轮转并保留固定数量。容器 stdout 则应限制 Docker logging driver，例如 Compose 服务中配置：

```yaml
services:
  api:
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"
```

这个例子把单个容器在节点上的日志上限控制在约 100 MB。实际值应结合并发量、节点磁盘和日志采集延迟确定。Kubernetes 通常由 kubelet 和容器运行时负责节点日志轮转，也必须检查集群实际配置，不能只依赖应用。

### 8.2 第二层：采集器缓冲

Fluent Bit、Vector、Promtail 等采集器应使用有上限的磁盘缓冲、批量发送和重试。第三方平台不可用时，无界内存队列会持续增长并导致 OOM；有界队列才能在达到上限后执行丢弃、阻塞或落盘策略。

当前项目的标准 `StreamHandler` 没有建立常驻的内存日志队列，因此日志本身通常不会直接把 Python 内存撑爆。更常见的风险是：

- stdout 或文件没有轮转，先把宿主机磁盘写满。
- 自行增加 `QueueHandler` 后使用无界队列，平台故障时内存不断增长。
- 把巨大请求体、异常对象或 ORM 实体放进日志，单条日志占用过大。
- 日志平台使用 `request_id/user_id` 等高基数字段建立索引，索引成本失控。

如果以后为了降低请求线程 I/O 引入异步日志队列，必须设置最大长度、满载策略和丢弃计数指标，不能使用无限队列。

### 8.3 第三层：日志平台保留策略

- Loki 在平台端配置 retention，让旧 chunk 自动过期；同时为不同租户设置吞吐和查询限制。
- Elasticsearch 使用 ILM（Index Lifecycle Management）按时间或大小 rollover，并在 delete phase 删除过期索引。
- 云日志服务使用 Log Group 的 retention 配置，不要保留为“永不过期”。

常见策略不是所有日志保存同样久。例如 DEBUG 不进入生产平台，普通应用日志保留 14～30 天，错误日志保留 30～90 天，安全审计按公司或法规要求保留 90～365 天。具体期限由排障周期、合规要求、存储预算和数据敏感等级共同决定。

### 8.4 容量估算和监控

部署前可以用下面的粗略公式估算每日原始日志量：

```text
每日容量 ≈ 平均每条字节数 × 每秒日志条数 × 86400
```

例如平均 800 字节、每秒 50 条，原始量约为 3.2 GB/天，再结合压缩率、索引开销、副本数和保留天数评估平台空间。线上至少监控磁盘使用率、日志写入速率、采集延迟、丢弃条数、队列长度和平台存储容量，并在到达硬上限前告警。

## 9. 日志告警建议

告警应基于一段时间内的错误率、P95/P99 延迟和关键依赖失败，而不是每一条 `ERROR` 都通知。日志用于解释事件，指标用于发现趋势，OpenTelemetry Trace 用于跨服务还原调用链，三者用途不同。

## 10. 与 OpenTelemetry 的关系

当前 `request_id` 是应用级关联 ID，适合单体系统排错；它不是 W3C Trace ID。将来拆分多个服务时，应启用 OpenTelemetry HTTP/SQLAlchemy instrumentation，并把 `trace_id`、`span_id` 注入 JSON 日志。日志平台再通过 Trace ID 跳转到 Tempo、Jaeger 或云 APM，就能看到跨服务耗时。即使启用 Trace，也可以保留 `request_id` 作为面向客户端的排错编号。
