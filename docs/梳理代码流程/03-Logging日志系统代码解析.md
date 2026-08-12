# Logging 日志系统代码解析

## 1. 这个模块解决什么问题

[app/core/logging.py](../../app/core/logging.py) 负责：

1. 统一文本日志和 JSON 日志格式。
2. 给同一次 HTTP 请求的所有日志自动添加 `request_id`。
3. 把日志输出到终端，或按 UTC 日期和文件大小轮转。
4. 清理超过保留天数的日志文件。
5. 控制第三方数据库 Logger 的噪声等级。

它不负责：

- 把日志写入业务数据库。
- 提供日志搜索 API。
- 自动给密码、Token、Cookie 脱敏。
- 把文件直接上传到 Loki/Elasticsearch。

生产推荐把 JSON 写到容器 stderr/stdout，再由日志采集器处理；本地文件模式适合学习和单机调试。

## 2. Python logging 的五个核心对象

先记住下面这条事件链：

```text
业务代码调用 Logger
  -> Logger 先按 Level 判断这条事件是否启用
  -> 未启用：立即结束，不创建 LogRecord
  -> 已启用：创建 LogRecord，并把 extra 合并成它的属性
  -> Logger 自己的 Filter（如果有）决定是否继续
  -> 沿 Logger 层级把 LogRecord 交给 Handler
  -> 每个 Handler 再检查自己的 Level 和 Filter
  -> Formatter 把 LogRecord 转为字符串
  -> Handler 写终端或文件
```

等级判断必须写在创建 `LogRecord` 前面。以 `LOG_LEVEL=INFO` 时的 `logger.debug(...)` 为例，
`Logger.isEnabledFor(logging.DEBUG)` 会先得到 `False`，后续的 LogRecord、Filter、Formatter 和
文件写入都不会发生。这也是使用 `%s` 延迟格式化的日志在禁用时开销较小的原因之一。

| 对象 | 本项目中的例子 | 作用 |
|---|---|---|
| Logger | `logging.getLogger("app.access")` | 业务代码记录事件的入口 |
| LogRecord | `logger.info()` 内部创建 | 一条日志事件的结构化对象 |
| Filter | `RequestContextFilter` | 注入 `request_id` |
| Formatter | `JsonFormatter` 或文本 Formatter | 把 LogRecord 转成最终字符串 |
| Handler | Stream/DailyFileHandler | 决定写到哪里 |

## 3. Logger 到底是什么

业务模块通常写：

```python
import logging

logger = logging.getLogger(__name__)
```

假设文件是 `app/services/posts.py`，`__name__` 就是：

```text
app.services.posts
```

Logger 是一个长期复用的日志入口对象。它主要保存：

- Logger 名称。
- 日志等级。
- 自己的 Handler。
- 是否向父 Logger 传播。

调用：

```python
logger.info("Post created", extra={"post_id": 12})
```

Logger 不只是直接打印字符串，而是先创建 LogRecord，再交给后续组件处理。

## 4. LogRecord 到底是什么

`LogRecord` 是“一条日志事件在 Python 内存中的对象”。它不是文本文件中的一行，也不是数据库
Model，而是 logging 在格式化之前保存事件全部信息的容器。

示例：

```python
logger.info(
    "HTTP request completed",
    extra={
        "method": "GET",
        "path": "/api/posts",
        "status_code": 200,
        "duration_ms": 12.5,
    },
)
```

logging 内部会创建近似下面的 LogRecord：

```text
record.name          = "app.access"
record.levelname     = "INFO"
record.levelno       = 20
record.msg           = "HTTP request completed"
record.args          = ()
record.created       = Unix 时间戳
record.pathname      = 调用日志的 Python 文件路径
record.lineno        = 调用日志的行号
record.exc_info      = None
record.method        = "GET"
record.path          = "/api/posts"
record.status_code   = 200
record.duration_ms   = 12.5
```

`extra` 字典中的键会变成 LogRecord 属性。因此 Formatter 可以读取：

```python
record.status_code
record.__dict__["status_code"]
```

### `msg`、`args` 和 `getMessage()`

如果代码写：

```python
logger.info("User %s logged in", user_id)
```

LogRecord 内部先保存：

```text
msg = "User %s logged in"
args = (user_id,)
```

调用：

```python
record.getMessage()
```

才得到最终文本：

```text
User 7 logged in
```

延迟格式化的好处是：如果日志等级被过滤，logging 不必提前完成字符串拼接。

## 5. `_STANDARD_RECORD_FIELDS` 的作用

```python
_STANDARD_RECORD_FIELDS = set(
    logging.makeLogRecord({}).__dict__
) | {"message", "asctime"}
```

逐步解释：

1. `logging.makeLogRecord({})` 创建一个使用默认字段的 LogRecord。
2. `.__dict__` 取得对象属性字典。
3. `set(...)` 只保留字段名称，并提供快速成员判断。
4. `|` 是集合并集，把 Formatter 可能增加的 `message/asctime` 也加入。

为什么需要它：JSON Formatter 想保留 `extra` 业务字段，但不希望把 logging 自带的几十个内部字段
全部重复写进 JSON。

```text
标准字段 pathname/lineno/args -> 通常不复制到 JSON 顶层
extra 字段 post_id/user_id     -> 保留到 JSON 顶层
```

## 6. 为什么异步请求不能使用普通全局 request_id

FastAPI 在同一线程的事件循环中并发处理请求：

```text
请求 A 执行一半 -> await 数据库
请求 B 开始执行 -> await Redis
请求 A 恢复执行
```

如果使用普通全局变量：

```python
current_request_id = "A"
```

请求 B 会把它改成 B，请求 A 恢复时也读到 B。

`threading.local()` 也不够，因为 A 和 B 可能在同一个线程，只是不同 asyncio Task。

## 7. ContextVar 是什么

```python
_request_id: ContextVar[str] = ContextVar(
    "request_id",
    default="-",
)
```

`ContextVar` 可以理解为“属于当前异步执行上下文的变量”。不同 Task 可以有自己的值：

```text
请求 A Task -> request_id=A
请求 B Task -> request_id=B
请求外日志  -> 默认 "-"
```

泛型标注 `ContextVar[str]` 表示这个上下文变量保存字符串。

## 8. `bind_request_id()` 和 Token

```python
def bind_request_id(request_id: UUID | str) -> Token[str]:
    return _request_id.set(str(request_id))
```

步骤：

1. 接收 UUID 或字符串。
2. `str(request_id)` 统一转换成文本。
3. `_request_id.set(...)` 给当前上下文绑定值。
4. `set()` 返回一个 ContextVar Token。

这里的 Token 不是 JWT。它只是 `contextvars` 用来记录“设置前是什么状态”的恢复凭据。

## 9. 为什么 reset 不能简单写成 `set("-")`

```python
def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)
```

假设存在嵌套绑定：

```text
外层原值 = outer
内层 set = inner
内层结束应该恢复 outer，不是固定恢复 "-"
```

`reset(token)` 会恢复到准确的旧值。FastAPI 中间件把它放进 `finally`，无论请求成功、HTTP 异常
还是未知异常都会清理。

## 10. RequestContextFilter 不是只用来“过滤”

```python
class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True
```

logging Filter 可以做两件事：

- 返回 False：丢弃这条日志。
- 修改 record 后返回 True：补充字段并继续输出。

本项目使用第二种。

`hasattr(record, "request_id")` 检查调用者是否已经通过 `extra` 显式提供 request_id。如果提供了，
保留显式值；没有才从 ContextVar 获取。

文本 Formatter 使用 `%(request_id)s`。如果 Filter 不保证该字段存在，格式化会抛 KeyError，真正
想记录的业务日志反而丢失。

## 11. JsonFormatter 逐步解析

核心方法：

```python
def format(self, record: logging.LogRecord) -> str:
```

输入是 LogRecord，输出必须是最终字符串。

### 基础字段

```python
payload = {
    "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
    "level": record.levelname,
    "logger": record.name,
    "message": record.getMessage(),
    "request_id": getattr(record, "request_id", "-"),
}
```

| 表达式 | 含义 |
|---|---|
| `record.created` | 日志事件创建时的 Unix 时间戳 |
| `datetime.fromtimestamp(..., UTC)` | 转成带 UTC 时区的 datetime |
| `.isoformat()` | 转成 `2026-08-11T04:00:00+00:00` |
| `record.levelname` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `record.name` | Logger 名称，例如 `app.access` |
| `record.getMessage()` | 合并 msg 和 args 后的最终消息 |
| `getattr(..., "-")` | 字段缺失时安全使用 `-` |

### 收集 extra 字段

```python
for field, value in record.__dict__.items():
    if field not in _STANDARD_RECORD_FIELDS and field not in payload:
        payload[field] = value
```

`record.__dict__.items()` 每次产生 `(字段名, 字段值)`。

条件的两个部分：

1. 不是 logging 标准字段。
2. 不覆盖 payload 已经定义的稳定字段。

所以 `post_id`、`method` 会保留，而调用者不能用 extra 覆盖 timestamp 或 level。

### 异常堆栈

```python
if record.exc_info:
    payload["exception"] = self.formatException(record.exc_info)
```

`logger.exception(...)` 或 `logger.error(..., exc_info=True)` 会保存异常类型、异常值和 traceback。
`formatException()` 把异常三元组转成可读调用栈。

堆栈只能写服务端日志，不能直接返回 HTTP 响应，否则可能泄露源码路径、SQL 和配置。

### 转成单行 JSON

```python
json.dumps(payload, ensure_ascii=False, default=str)
```

| 参数 | 作用 |
|---|---|
| `ensure_ascii=False` | 中文直接显示，不转成 `\u4e2d` |
| `default=str` | UUID/datetime 等非 JSON 原生对象用 str 转换 |
| 默认无 indent | 每条日志保持一行，采集器按换行切分 |

## 12. 文本和 JSON 输出示例

业务调用：

```python
logger.info(
    "HTTP request completed",
    extra={"method": "GET", "path": "/", "status_code": 200},
)
```

文本格式：

```text
2026-08-11 12:00:00 INFO app.access [request_id=abc] HTTP request completed
```

JSON 格式：

```json
{"timestamp":"2026-08-11T04:00:00+00:00","level":"INFO","logger":"app.access","message":"HTTP request completed","request_id":"abc","method":"GET","path":"/","status_code":200}
```

## 13. Handler 是什么

Formatter 只负责“变成什么字符串”，Handler 负责“写到哪里”。

本项目有两种输出：

```text
LOG_TO_FILE=false -> logging.StreamHandler
LOG_TO_FILE=true  -> DailyFileHandler
```

无参数 `StreamHandler()` 默认写 `sys.stderr`。Docker 一般同时采集 stdout 和 stderr。

## 14. DailyFileHandler 为什么还包含 RotatingFileHandler

Python 标准 `RotatingFileHandler` 擅长按大小轮转，但项目还希望文件名按日期：

```text
2026-08-11.log
2026-08-11.log.1
2026-08-11.log.2
2026-08-12.log
```

所以 `DailyFileHandler` 是外层协调器：

- 自己判断 UTC 日期是否变化。
- 每天创建一个内部 `RotatingFileHandler`。
- 内部 Handler 负责当天按大小轮转。
- 日期切换时关闭旧内部 Handler。

变量：

| 变量 | 含义 |
|---|---|
| `directory` | 日志根目录 Path |
| `max_bytes` | 单文件最大字节数 |
| `files_per_day` | 主文件加备份的每日总数 |
| `retention_days` | 保留多少个 UTC 日期 |
| `_active_date` | 当前内部 Handler 正在写哪个日期 |
| `_delegate` | 真正写文件的 RotatingFileHandler |

`delegate` 可以理解为“被外层委托执行实际写入的对象”。

## 15. `_open_for_date()` 每一步

```python
if self._delegate is not None:
    self._delegate.close()
```

如果旧文件仍打开，先关闭文件句柄。Windows 下未关闭文件可能无法删除或重命名。

```python
self.directory.mkdir(parents=True, exist_ok=True)
```

| 参数 | 作用 |
|---|---|
| `parents=True` | 父目录不存在时一起创建 |
| `exist_ok=True` | 目录已经存在时不要报 FileExistsError |

```python
target = self.directory / f"{current_date.isoformat()}.log"
```

Path 的 `/` 运算符用于拼接路径，不是数学除法。

内部 Handler：

```python
RotatingFileHandler(
    target,
    maxBytes=self.max_bytes,
    backupCount=self.files_per_day - 1,
    encoding="utf-8",
)
```

`backupCount` 不包含当前主文件。例如 `files_per_day=5`：

```text
当前文件 1 个 + 备份 .1～.4 共 4 个 = 5 个
```

理论单日磁盘上限约为：

```text
max_bytes × files_per_day
```

## 16. 日志过期清理逐行解析

```python
oldest_kept_date = current_date - timedelta(
    days=self.retention_days - 1
)
```

减一是因为包含今天。例如保留 3 天：今天、昨天、前天，共 3 个日期。

```python
for path in self.directory.glob("????-??-??.log*"):
```

`Path.glob()` 按模式查找文件：

- `?` 匹配一个字符。
- `*` 匹配任意数量字符。
- 能匹配主文件 `.log` 和轮转文件 `.log.1`。
- 每次循环的 `path` 是 Path 对象。

```python
file_date = date.fromisoformat(path.name[:10])
```

逐步解释：

1. `path.name` 只取文件名，不含目录。
2. `[:10]` 切片取 `YYYY-MM-DD`。
3. `date.fromisoformat()` 转成 date，便于比较日期。

如果文件名不是真实日期，例如 `2026-99-99.log`，会抛 ValueError。代码捕获后 `continue`，表示
跳过当前文件，继续检查下一个，避免误删不属于该 Handler 的文件。

## 17. `path.unlink(missing_ok=True)` 精确含义

```python
path.unlink(missing_ok=True)
```

等价业务含义：

```text
删除 path 指向的文件；如果文件已经不存在，不要报错。
```

`unlink()` 名字来自 Unix 的“移除文件系统链接”。对于普通文件，可以理解为删除文件。

`missing_ok=True` 的作用：

```text
文件存在   -> 删除
文件不存在 -> 直接返回，不抛 FileNotFoundError
```

为什么适合清理代码：另一个进程、管理员或前一次操作可能已经删除文件。清理的目标是“文件最终
不存在”，已经不存在就是成功，这叫幂等。

它不代表忽略所有错误：权限不足、目录只读、文件被占用等 OSError 仍可能抛出。

项目头像清理中的 `target.unlink(missing_ok=True)` 含义完全相同。

## 18. `emit()` 是什么时候执行的

Logger 找到 Handler 后，先比较 `record.levelno` 与 Handler 自身等级，再由
`Handler.handle(record)` 执行 Filter；通过后才会调用 `emit(record)`。

DailyFileHandler 的步骤：

```text
1. 取得当前 UTC 日期
2. 检查 `_delegate` 是否存在
3. 检查日期是否变化
4. 必要时 `_open_for_date()`
5. 把外层 Formatter 同步给内部 Handler
6. `delegate.emit(record)` 真正写文件
```

为什么同步 Formatter：Formatter 配置在外层 DailyFileHandler 上，但实际写入由新创建的 delegate
完成；日期切换后新的 delegate 也必须得到同一个 Formatter。

## 19. 为什么 `emit()` 捕获宽泛 Exception

通常不建议写：

```python
except Exception:
```

但 logging Handler 是基础设施边界。Formatter、编码、文件权限、磁盘空间都可能抛出不同异常。
日志失败不应该把原本成功的创建文章请求变成 500，所以统一调用：

```python
self.handleError(record)
```

开发模式下 logging 可能把内部错误写到 stderr；业务请求继续执行。

## 20. `close()` 为什么必须释放 delegate

```python
if self._delegate is not None:
    self._delegate.close()
    self._delegate = None
super().close()
```

先关闭真实文件句柄，再清空引用，最后执行父类 Handler 的关闭逻辑。应用退出、测试恢复配置和热
重载时都需要释放文件，否则可能重复写入或长期占用文件。

## 21. `_handlers(settings)` 怎样选择输出

文件模式：

```python
return [DailyFileHandler(...)]
```

终端模式：

```python
return [logging.StreamHandler()]
```

返回 list 是为了让 `configure_logging()` 使用统一循环。当前每种模式只有一个 Handler，未来可
扩展为同时写控制台和告警系统，但要注意重复日志和业务请求阻塞。

## 22. `configure_logging()` 组装流程

```text
Settings
  -> _handlers() 创建输出目标
  -> 根据 LOG_FORMAT 创建 Formatter
  -> 每个 Handler 添加 RequestContextFilter
  -> 每个 Handler 设置 Formatter
  -> 设置 Root Logger Level
  -> 关闭旧 Root Handlers
  -> 挂载新 Handlers
  -> 限制数据库 Logger 等级
```

为什么配置 Root Logger：Python Logger 按点号形成父子层级：

```text
app.services.posts
  -> app.services
  -> app
  -> root
```

子 Logger 默认 `propagate=True`，自己的 LogRecord 会向上传播到 Root Handler。因此各 Service
只需要 `getLogger(__name__)`，不重复创建文件 Handler。

## 23. 为什么先关闭旧 Handler

```python
for old_handler in root_logger.handlers[:]:
    root_logger.removeHandler(old_handler)
    old_handler.close()
```

`handlers[:]` 创建列表副本。因为循环中会修改原列表，直接遍历原列表可能跳过元素。

重新配置前删除旧 Handler 可以避免：

```text
一条 logger.info -> 旧 Handler 输出一次 + 新 Handler 输出一次
```

只 remove 不 close 会遗留打开的文件句柄，所以两步都要执行。

## 24. 日志等级怎样工作

从低到高：

```text
DEBUG < INFO < WARNING < ERROR < CRITICAL
```

Root 设置 `INFO` 时，继承 Root 等级的项目 Logger 通常忽略 DEBUG，保留 INFO 及以上。

项目单独设置：

```python
logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

即使项目开 DEBUG，数据库驱动日常仍只输出警告和错误，避免 cursor、fetch、commit 细节淹没
业务日志。调查 SQL 时可临时调整，但不能把参数中的密码和敏感数据长期记录。

## 25. 一次 HTTP 请求的日志完整时序

[app/main.py](../../app/main.py) 启动时：

```python
settings = get_settings()
configure_logging(settings)
access_logger = logging.getLogger("app.access")
```

请求进入中间件：

```text
1. uuid4() 生成 request_id
2. 保存到 request.state，供响应和异常处理器读取
3. bind_request_id() 绑定 ContextVar
4. perf_counter() 记录单调开始时间
5. await call_next(request) 执行 Router/Service
6. 响应写 X-Request-ID
7. finally 中 access_logger.info()
8. duration_ms = 当前 perf_counter - started_at
9. reset_request_id(token)
```

`perf_counter()` 专门测量时间间隔，不受系统日期调整影响，适合计算耗时。

访问日志 `extra`：

```python
{
    "method": request.method,
    "path": request.url.path,
    "status_code": status_code,
    "duration_ms": ...,
}
```

有意只记录 path，不记录 query string 和请求体，避免搜索词、Token、密码进入长期日志。

## 26. 正确记录业务日志

推荐：

```python
logger.info(
    "Post published",
    extra={
        "post_id": post.id,
        "user_id": current_user.id,
        "action": "publish",
    },
)
```

异常：

```python
try:
    ...
except SomeExpectedError:
    logger.exception(
        "Post publishing failed",
        extra={"post_id": post_id},
    )
    raise
```

不要记录：

```python
logger.info("login", extra={"password": password})
logger.info("token=%s", access_token)
logger.info("cookie=%s", request.headers.get("cookie"))
logger.info("database=%s", complete_database_url)
```

request_id 会自动注入；user_id/post_id 不会自动出现，需要业务代码通过 extra 提供。

## 27. 配置项逐个解释

| 配置 | 当前含义 | 调大/切换后的影响 |
|---|---|---|
| `LOG_LEVEL` | 最低关注等级 | DEBUG 信息更多，也更占磁盘/采集额度 |
| `LOG_FORMAT` | text 或 json | JSON 适合平台检索，text 适合本地阅读 |
| `LOG_TO_FILE` | 是否本地写文件 | 容器生产建议 false |
| `LOG_DIRECTORY` | 文件目录 | 需要进程写权限 |
| `LOG_MAX_BYTES` | 单文件大小 | 越大越少轮转，但单文件更难处理 |
| `LOG_FILES_PER_DAY` | 每天主文件+备份总数 | 决定单日磁盘上限 |
| `LOG_RETENTION_DAYS` | 保留 UTC 日期数 | 越长占用越多磁盘 |

## 28. 当前实现边界

1. 文件写入是同步 I/O，高流量时可能短暂阻塞事件循环。
2. 多 worker 同时写同一轮转文件可能竞争，不适合多进程生产文件日志。
3. `default=str` 保证 JSON 不轻易失败，但业务字段仍应传简单标量。
4. 没有自动敏感字段脱敏，日志调用者必须遵守安全约定。
5. ContextVar 自动关联当前请求；单独创建长期后台 Task 时要明确是否继续沿用请求 ID。
6. StreamHandler 默认写 stderr。如果采集器只收 stdout，需要显式调整采集配置或 Handler stream。

## 29. 测试覆盖和调试

[tests/test_logging.py](../../tests/test_logging.py) 验证：

- ContextVar 注入和 reset。
- 显式 request_id 不被覆盖。
- JSON extra 字段。
- 数据库 Logger 等级。
- UTC 日期文件和大小轮转。
- INFO/ERROR 在同一文件中的时间顺序。
- HTTP 响应包含 `X-Request-ID`。

调试一条日志时按顺序检查：

```text
Logger 是否启用该等级
  -> 是否向 Root 传播
  -> Handler 是否存在
  -> Filter 是否注入 request_id
  -> Formatter 是否能序列化 extra
  -> Stream/目录是否可写
```
