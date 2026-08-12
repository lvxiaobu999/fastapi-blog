"""应用日志基础设施。

本模块负责日志格式、请求上下文和输出目标，不负责把日志保存到业务数据库，也不提供
日志查询 API。生产环境应优先把 JSON 写到标准输出，再由容器平台的日志采集器发送到
Loki、Elasticsearch 等专用系统；本地文件轮转只作为单机部署的可选方案。
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import Settings

# ContextVar 可以理解为“属于当前执行上下文的变量”。FastAPI 会在同一个线程的事件循环中
# 并发执行许多 async 请求，普通全局变量会被这些请求互相覆盖，threading.local 又只能
# 区分线程、不能区分同一线程内的不同 asyncio Task。ContextVar 会随当前 Task 传播，
# 因而请求 A 和请求 B 可以各自读取自己的 request_id。请求之外的启动日志使用默认值 "-"。
_request_id: ContextVar[str] = ContextVar("request_id", default="-")

# LogRecord 自带的字段不是业务上下文，重复写入 JSON 会制造大量无用数据。开发者通过
# extra 传入的其他字段会被保留，因此 post_id、user_id、action 等都可以独立检索。
_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def bind_request_id(request_id: UUID | str) -> Token[str]:
    """把请求 ID 绑定到当前异步任务，并返回可用于恢复旧值的 Token。

    ``ContextVar.set()`` 不只是赋值，还会返回一个记录“赋值前状态”的 Token。HTTP
    中间件在调用 Router 前执行本函数，因此 Router、Service 以及它们 await 的下层协程
    都能读取这个 ID，而业务函数不需要把 request_id 作为参数逐层传递。

    返回值不是“上一个 request_id”字符串，而是只能交给 ``reset()`` 使用的上下文恢复凭据。
    """

    return _request_id.set(str(request_id))


def reset_request_id(token: Token[str]) -> None:
    """用 bind 时得到的 Token 恢复旧上下文，避免请求 ID 泄漏。

    这里不能简单地执行 ``set("-")``：上下文可能存在嵌套绑定，Token 能准确恢复绑定前
    的值。中间件把 reset 放在 finally 中，成功、HTTP 错误和未知异常都会执行清理。
    若不清理，当前 Task 后续执行的后台逻辑可能错误地沿用已经结束的请求 ID。
    """

    _request_id.reset(token)


class RequestContextFilter(logging.Filter):
    """在日志格式化之前，把当前请求 ID 注入每一个 LogRecord。

    logging 的 Filter 不只用于“过滤掉日志”，也可以补充字段。Handler 收到 LogRecord 后
    会先调用本 Filter；返回 True 表示允许继续输出，False 才表示丢弃。若调用者已经通过
    ``extra={"request_id": ...}`` 显式提供 ID，则保留显式值，避免覆盖异常处理器等边界
    场景传入的可靠标识。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Formatter 引用了 request_id，所以请求外日志也必须补上默认值，否则文本格式化
        # 会因为缺少字段抛 KeyError，反而让真正的业务日志丢失。
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """把 LogRecord 转成单行 JSON，供日志采集器按稳定字段解析和检索。"""

    def format(self, record: logging.LogRecord) -> str:
        # LogRecord 是 logger.info/error 创建的内部事件对象，包含级别、Logger 名称、
        # 创建时间、消息模板、参数、异常信息，以及 extra 注入的业务字段。
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # extra 字段保存在 record.__dict__ 中。排除 logging 自带字段后，post_id、user_id、
        # method 等业务上下文会成为 JSON 顶层字段，日志平台无需解析 message 就能筛选。
        for field, value in record.__dict__.items():
            if field not in _STANDARD_RECORD_FIELDS and field not in payload:
                payload[field] = value
        # logger.exception() 或 exc_info=True 会把异常三元组放进 LogRecord。调用栈只写服务端
        # 日志，不能塞进 HTTP 响应，否则会暴露源码路径、SQL 或内部配置。
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # 每条日志只占一行，采集器可以用换行符准确切分事件；default=str 防止 UUID、
        # datetime 等常见对象无法 JSON 序列化，但业务代码仍应优先传简单标量。
        return json.dumps(payload, ensure_ascii=False, default=str)


class DailyFileHandler(logging.Handler):
    """按 UTC 日期写文件，并在单日内按大小继续轮转。

    configure_logging 传入日志根目录，例如 ``logs``。当前文件名始终是
    ``YYYY-MM-DD.log``；超过 max_bytes 后由 RotatingFileHandler 生成 ``.1``、``.2``。
    日期变化时关闭旧文件并打开新文件，同时清理 retention_days 之前的历史日期。
    """

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int,
        files_per_day: int,
        retention_days: int,
    ) -> None:
        super().__init__()
        self.directory = directory
        self.max_bytes = max_bytes
        self.files_per_day = files_per_day
        self.retention_days = retention_days
        self._active_date: date | None = None
        self._delegate: RotatingFileHandler | None = None

    def _open_for_date(self, current_date: date) -> RotatingFileHandler:
        """切换到指定日期文件；只在首次写入或 UTC 日期变化时执行。"""

        if self._delegate is not None:
            self._delegate.close()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._delete_expired_files(current_date)
        target = self.directory / f"{current_date.isoformat()}.log"
        # backupCount 不包含正在写入的主文件，因此需要减一。例如配置 5 表示当前文件
        # 加 .1～.4 共五个文件，单等级单日磁盘上限约为 max_bytes × 5。
        self._delegate = RotatingFileHandler(
            target,
            maxBytes=self.max_bytes,
            backupCount=self.files_per_day - 1,
            encoding="utf-8",
        )
        self._active_date = current_date
        return self._delegate

    def _delete_expired_files(self, current_date: date) -> None:
        """删除超出日期保留窗口的主文件和同日期大小轮转文件。"""

        oldest_kept_date = current_date - timedelta(days=self.retention_days - 1)
        for path in self.directory.glob("????-??-??.log*"):
            try:
                file_date = date.fromisoformat(path.name[:10])
            except ValueError:
                # 非本 Handler 命名的文件不自动删除，避免误伤人工放入目录的资料。
                continue
            if file_date < oldest_kept_date:
                path.unlink(missing_ok=True)

    def emit(self, record: logging.LogRecord) -> None:
        """把已通过 Filter 的记录写入今天的大小轮转文件。"""

        try:
            current_date = datetime.now(UTC).date()
            delegate = self._delegate
            if delegate is None or self._active_date != current_date:
                delegate = self._open_for_date(current_date)
            # Formatter 配置在外层 Handler 上；日期切换创建新 delegate 后同步过去。
            delegate.setFormatter(self.formatter)
            delegate.emit(record)
        # logging.Handler.emit 的契约要求日志后端故障不能中断业务请求；Formatter 和底层
        # 文件写入可能抛出不同异常，因此在这个边界统一交给 handleError 处理。
        except Exception:  # noqa: BLE001
            self.handleError(record)

    def close(self) -> None:
        """应用退出或重新配置时关闭当前文件句柄。"""

        if self._delegate is not None:
            self._delegate.close()
            self._delegate = None
        super().close()


def _handlers(settings: Settings) -> list[logging.Handler]:
    """创建终端 Handler，或一个按日期和大小轮转的完整日志文件 Handler。"""

    if settings.log_to_file:
        # Root Logger 已经依据 LOG_LEVEL 过滤事件。所有通过阈值的等级写入同一日期文件，
        # 排错时可以按 request_id 直接看到 INFO -> WARNING -> ERROR 的完整时间顺序。
        return [
            DailyFileHandler(
                Path(settings.log_directory),
                max_bytes=settings.log_max_bytes,
                files_per_day=settings.log_files_per_day,
                retention_days=settings.log_retention_days,
            )
        ]
    # 未指定 stream 时默认写 sys.stderr。容器会采集 stdout/stderr，因此生产环境无需让
    # FastAPI 自己调用 Loki/ELK HTTP API，也不会把第三方平台延迟加到业务请求中。
    return [logging.StreamHandler()]


def configure_logging(settings: Settings) -> None:
    """组装 Level → Handler → Filter → Formatter，并挂到 Root Logger。

    应在创建 FastAPI 实例前调用一次，保证应用启动及后续模块日志使用同一套配置。
    各模块只负责 ``logging.getLogger(__name__)`` 和记录事件，不负责重复创建文件 Handler。
    """

    handlers = _handlers(settings)
    formatter: logging.Formatter
    if settings.log_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
        )
    for handler in handlers:
        handler.addFilter(RequestContextFilter())
        handler.setFormatter(formatter)

    # 不传 name 得到 Root Logger。Python Logger 按点号形成层级，例如 app.access 的父级
    # 是 app，最终是 root。子 Logger 默认 propagate=True，所以 access_logger.info()
    # 创建的 LogRecord 会向上传播到这里，并使用这一份 Handler 输出。
    root_logger = logging.getLogger()
    # Root Level 是总开关：低于此级别的事件在进入 Handler 前就会被忽略。
    root_logger.setLevel(settings.log_level)
    # 清除导入或开发热重载留下的旧 Handler，避免同一条日志重复输出。仅从列表移除不会
    # 自动关闭文件句柄，因此先逐个 close，Windows 下尤其要避免旧日志一直被占用。
    for old_handler in root_logger.handlers[:]:
        root_logger.removeHandler(old_handler)
        old_handler.close()
    for handler in handlers:
        root_logger.addHandler(handler)

    # Root Logger 使用 DEBUG 时，第三方数据库库也会继承 DEBUG，从而逐条输出 cursor、
    # execute、fetch、commit 等底层细节。这些信息日常排错价值较低，却会淹没应用日志。
    # 单独把两个命名空间提升为 WARNING 后，app.* 仍遵循 LOG_LEVEL=DEBUG，而数据库驱动
    # 只有出现警告或错误才输出。需要调查底层 SQL 时可以临时把对应 Logger 改回 DEBUG。
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
