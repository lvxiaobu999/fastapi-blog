"""应用日志基础设施。

本模块负责日志格式、请求上下文和输出目标，不负责把日志保存到业务数据库，也不提供
日志查询 API。生产环境应优先把 JSON 写到标准输出，再由容器平台的日志采集器发送到
Loki、Elasticsearch 等专用系统；本地文件轮转只作为单机部署的可选方案。
"""

from __future__ import annotations

import json
import logging
import logging.config
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.config import Settings

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

# LogRecord 自带的字段不是业务上下文，重复写入 JSON 会制造大量无用数据。开发者通过
# extra 传入的其他字段会被保留，因此 post_id、user_id、action 等都可以独立检索。
_STANDARD_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


def bind_request_id(request_id: UUID | str) -> Token[str]:
    """把请求 ID 绑定到当前异步任务，使本次请求中的日志自动带上同一标识。"""

    return _request_id.set(str(request_id))


def reset_request_id(token: Token[str]) -> None:
    """请求结束时恢复上下文，防止连接复用时把上一个请求 ID 带到下一个请求。"""

    _request_id.reset(token)


class RequestContextFilter(logging.Filter):
    """为没有显式 request_id 的日志记录补充当前请求上下文。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """把日志转换成一行 JSON，方便 Loki、ELK 和云日志平台按字段检索。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for field, value in record.__dict__.items():
            if field not in _STANDARD_RECORD_FIELDS and field not in payload:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _handler(settings: Settings) -> logging.Handler:
    """根据配置创建 stdout 或按天轮转的文件 Handler。"""

    if settings.log_to_file:
        log_path = Path(settings.log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # backupCount 表示保留多少个历史文件；轮转发生时会自动删除更早的文件。
        return TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=settings.log_retention_days,
            encoding="utf-8",
            utc=True,
        )
    return logging.StreamHandler()


def configure_logging(settings: Settings) -> None:
    """初始化应用日志；应在创建 FastAPI 实例前调用一次。"""

    handler = _handler(settings)
    handler.addFilter(RequestContextFilter())
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s] %(message)s"
            )
        )

    print(f"LOG_LEVEL--->{settings.log_level}")

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    # 清除导入或开发热重载留下的旧 Handler，避免同一条日志重复输出。
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
