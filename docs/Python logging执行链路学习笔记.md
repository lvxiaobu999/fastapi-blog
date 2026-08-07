# Python logging 执行链路学习笔记

## 1. 先认识五个核心对象

Python 标准日志不是一个 `print()` 函数，而是一条由多个对象协作的流水线：

| 对象 | 职责 | 本项目中的例子 |
| --- | --- | --- |
| Logger | 接收业务代码的日志调用，并创建 LogRecord | `logging.getLogger("app.access")` |
| LogRecord | 表示“一次日志事件”的数据对象 | message、level、logger、request_id、post_id |
| Filter | 决定事件是否继续，并可在输出前补字段 | `RequestContextFilter` 注入 request_id |
| Formatter | 把 LogRecord 转换成最终文本 | `JsonFormatter` 或文本 Formatter |
| Handler | 决定日志写到哪里 | stderr 或按等级/日期/大小轮转文件 |

本项目的一条访问日志执行链如下：

```text
access_logger.info("HTTP request completed", extra={...})
  -> Logger 检查 INFO 是否达到有效等级
  -> Logger 创建 LogRecord
  -> LogRecord 沿 app.access -> app -> root 向上传播
  -> Root Logger 将 LogRecord 交给 Handler
  -> RequestContextFilter 给 LogRecord 补 request_id
  -> Formatter 生成文本或单行 JSON
  -> Handler 写入 stderr 或轮转文件
  -> Docker/采集器再把生产 JSON 发送到 Loki/ELK
```

## 2. `getLogger("app.access")` 的参数有什么用

`logging.getLogger(name)` 按名称取得 Logger。相同名称在同一进程中返回同一个 Logger 对象，因此模块不需要到处传递 Logger 实例。

名称使用点号表达父子层级：

```text
root
└── app
    ├── access
    ├── services.posts
    └── exception_handlers
```

`app.access` 是一个语义稳定的“访问日志频道”，不是要显示给用户的消息。选择这个名称有三个作用：

1. JSON 的 `logger` 字段会等于 `app.access`，Loki/ELK 可以只查询访问日志。
2. 它默认 `propagate=True`，事件会交给父级，最终使用 Root Logger 的 Handler，不必重复配置输出文件。
3. 将来可以对 `app.access` 单独设置等级、关闭传播或绑定独立 Handler，而不影响业务异常日志。

业务模块通常使用 `logging.getLogger(__name__)`。例如 `app.services.posts` 模块会自动得到同名 Logger，看到日志名称就能找到代码来源。访问日志横跨所有模块，所以使用专门的 `app.access` 名称更清楚。

## 3. ContextVar 解决什么问题

FastAPI 是异步并发应用。一个线程的事件循环可能交错处理多个请求：

```text
请求 A 设置 request_id=A -> await 数据库
请求 B 设置 request_id=B -> await 数据库
请求 A 恢复执行并写日志
```

若 request_id 使用普通全局变量，请求 A 最后可能读到 B。`threading.local()` 也不够，因为 A、B 可能就在同一个线程，只是属于不同 asyncio Task。

`ContextVar` 保存的是“当前执行上下文中的值”。创建新 Task 时上下文会被复制，`await` 后恢复当前 Task 时也会恢复它自己的上下文。因此业务函数无需增加 request_id 参数：

```python
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
```

默认值 `-` 表示这条日志不属于某个 HTTP 请求，例如应用启动、迁移或后台初始化日志。

## 4. 为什么必须 `reset_request_id`

`ContextVar.set(value)` 返回一个 Token，Token 记录设置之前的状态：

```python
token = _request_id.set("request-A")
try:
    await call_next(request)
finally:
    _request_id.reset(token)
```

必须 reset 有三个原因：

1. 请求结束后，当前 Task 可能继续执行清理或框架逻辑，不能继续冒充该请求。
2. 测试、嵌套调用或复用上下文时，不清理会让后续日志携带旧 ID。
3. `reset(token)` 能恢复“设置前的值”，比粗暴地 `set("-")` 更适合嵌套上下文。

清理放在 `finally` 中，保证成功、异常和提前返回都执行。这与数据库 Session、文件句柄和锁的清理原则相同：资源或上下文的获取与释放必须成对出现。

## 5. RequestContextFilter 做什么

业务层希望这样写：

```python
logger.info("Post published", extra={"post_id": post.id})
```

它不应每次手动读取和传递 request_id。`RequestContextFilter` 在 Handler 格式化前统一执行：

```text
LogRecord 没有 request_id -> 从 ContextVar 读取并补上
LogRecord 已有 request_id -> 保留显式值
返回 True -> 允许 Handler 继续输出
返回 False -> 丢弃本条日志
```

这里的 Filter 实际用于“丰富日志上下文”，而不是删除日志。它还保证请求外日志拥有 `request_id="-"`，否则文本 Formatter 读取不存在的 `%(request_id)s` 时会报错。

## 6. JsonFormatter 如何处理 `extra`

调用 Logger 时，`extra` 中的字段会被合并到 LogRecord：

```python
logger.info(
    "Post published",
    extra={"post_id": 42, "user_id": 7, "action": "publish"},
)
```

`JsonFormatter` 排除 LogRecord 自带的内部字段，保留业务扩展字段，最终得到：

```json
{
  "timestamp": "2026-08-07T08:00:00+00:00",
  "level": "INFO",
  "logger": "app.services.posts",
  "message": "Post published",
  "request_id": "...",
  "post_id": 42,
  "user_id": 7,
  "action": "publish"
}
```

日志平台可以直接筛选 `post_id=42`，不需要从一长段 message 中解析数字。`exc_info` 存在时，Formatter 还会增加 `exception` 调用栈，但该内容只留在服务端。

## 7. Level 在哪里生效

本项目把 `LOG_LEVEL` 设置到 Root Logger。以 `LOG_LEVEL=INFO` 为例：

```text
DEBUG -> 低于 INFO，Logger 丢弃
INFO/WARNING/ERROR/CRITICAL -> 继续进入 Handler
```

Level 越高，输出越少。生产环境设置 `INFO` 并不是“只记录正常日志”，而是记录 INFO 及以上所有事件。若设为 `WARNING`，当前 `access_logger.info()` 也会消失。

Logger 和 Handler 都可以设置 Level。事件必须同时通过两者才会输出。当前项目只设置 Root Logger Level，让配置保持简单；将来如果访问日志量太大，可以给 `app.access` 或它的专属 Handler 设置不同等级或采样策略。

## 8. Handler 和 Formatter 如何配合

Handler 回答“写到哪里”，Formatter 回答“写成什么样”：

```text
LOG_TO_FILE=false -> StreamHandler -> stderr
LOG_TO_FILE=true  -> DailyFileHandler -> logs/YYYY-MM-DD.log

LOG_FORMAT=text   -> 人类易读文本
LOG_FORMAT=json   -> 日志平台易解析的单行 JSON
```

文件模式把所有达到全局阈值的事件写入同一个 UTC 日期文件，保留完整请求顺序；同一天再按单文件大小轮转，最后按保留天数清理历史日期。容器生产环境推荐 stderr/stdout 加外部采集器，因为多 Worker 共同写一个轮转文件可能发生竞争。

## 9. 一次 HTTP 请求的完整时间顺序

```text
1. Python 导入 app.main
2. get_settings() 读取 LOG_* 配置
3. configure_logging() 创建 Handler、Filter、Formatter 并挂到 root
4. 浏览器请求进入 add_request_id 中间件
5. uuid4() 生成服务端 request_id
6. bind_request_id() 把 ID 放入当前 Task 的 ContextVar
7. call_next() 执行 Router -> Service -> ORM
8. 期间任意应用 Logger 创建的日志都会经 Filter 获得同一个 request_id
9. 响应写入 X-Request-ID
10. access_logger 记录状态码和耗时
11. finally 中 reset_request_id() 恢复旧上下文
```

如果 Router 或 Service 抛出未知异常，统一异常处理器使用 `logger.error(..., exc_info=...)` 保存调用栈，并显式携带 `request.state.request_id`。客户端只收到安全的 500 响应和请求 ID，不会看到调用栈。

## 10. 常见误区

- `getLogger("app.access")` 不会创建日志文件；创建文件的是 Handler。
- Logger 名称不是文件名，而是事件来源与配置层级。
- Filter 不一定删除日志，也可以补充上下文字段。
- ContextVar 不是数据库或缓存，进程重启后不会保留数据。
- request_id 只关联一次请求，不等于跨服务 OpenTelemetry trace_id。
- 不要在配置完成后保留 `print()`，它没有 Level、request_id、JSON 格式和统一输出策略。
- 不要在业务模块各自添加 FileHandler，否则容易重复输出、文件句柄过多且配置失控。
