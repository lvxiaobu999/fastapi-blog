"""结构化日志与 HTTP 请求追踪测试。"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.logging import (
    DailyChannelFileHandler,
    JsonFormatter,
    MinimumLevelFilter,
    RequestContextFilter,
    bind_request_id,
    configure_logging,
    reset_request_id,
)
from app.core.config import get_settings

pytestmark = pytest.mark.anyio


def test_json_log_includes_request_context_and_access_fields() -> None:
    """同一异步上下文的日志应自动携带 request_id，并保留访问日志字段。"""

    current_request_id = uuid4()
    token = bind_request_id(current_request_id)
    try:
        record = logging.LogRecord(
            "app.access",
            logging.INFO,
            __file__,
            1,
            "HTTP request completed",
            (),
            None,
        )
        record.method = "GET"
        record.path = "/api/posts"
        record.status_code = 200
        record.duration_ms = 12.5
        record.post_id = 42
        RequestContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
    finally:
        reset_request_id(token)

    assert payload["request_id"] == str(current_request_id)
    assert payload["method"] == "GET"
    assert payload["path"] == "/api/posts"
    assert payload["status_code"] == 200
    assert payload["duration_ms"] == 12.5
    assert payload["post_id"] == 42


def test_request_context_reset_restores_default_value() -> None:
    """请求结束后必须恢复默认上下文，避免后续日志错误沿用旧 request_id。"""

    token = bind_request_id(uuid4())
    reset_request_id(token)
    record = logging.LogRecord("app.service", logging.INFO, __file__, 1, "done", (), None)

    RequestContextFilter().filter(record)

    assert record.request_id == "-"


def test_context_filter_keeps_explicit_request_id() -> None:
    """异常边界显式传入的 request_id 比当前上下文更可靠，不应被 Filter 覆盖。"""

    record = logging.LogRecord("app.error", logging.ERROR, __file__, 1, "failed", (), None)
    record.request_id = "explicit-id"

    RequestContextFilter().filter(record)

    assert record.request_id == "explicit-id"


def test_noisy_database_loggers_are_limited_to_warning() -> None:
    """应用开启 DEBUG 时，数据库驱动仍应只输出需要关注的警告和错误。"""

    assert logging.getLogger("aiosqlite").level == logging.WARNING
    assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING


def test_error_channel_accepts_error_and_critical_only() -> None:
    """故障副本接收 ERROR/CRITICAL，但不会混入普通 INFO。"""

    error_filter = MinimumLevelFilter(logging.ERROR)
    info_record = logging.LogRecord("app", logging.INFO, __file__, 1, "info", (), None)
    error_record = logging.LogRecord("app", logging.ERROR, __file__, 1, "error", (), None)
    critical_record = logging.LogRecord("app", logging.CRITICAL, __file__, 1, "down", (), None)

    assert error_filter.filter(info_record) is False
    assert error_filter.filter(error_record) is True
    assert error_filter.filter(critical_record) is True


def test_daily_handler_uses_channel_directory_date_and_size_rotation(tmp_path: Path) -> None:
    """文件名按 UTC 日期生成，并在单日内容超限时创建大小轮转备份。"""

    handler = DailyChannelFileHandler(
        tmp_path / "app",
        max_bytes=80,
        files_per_day=2,
        retention_days=14,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    try:
        for _ in range(4):
            handler.emit(logging.LogRecord("app", logging.INFO, __file__, 1, "x" * 40, (), None))
    finally:
        handler.close()

    today = datetime.now(UTC).date().isoformat()
    assert (tmp_path / "app" / f"{today}.log").exists()
    assert (tmp_path / "app" / f"{today}.log.1").exists()


def test_file_mode_keeps_complete_app_timeline_and_error_copy(tmp_path: Path) -> None:
    """INFO 只进入 app；ERROR 同时进入 app 完整链路和 error 故障副本。"""

    original_settings = get_settings()
    settings = original_settings.model_copy(
        update={
            "log_level": "INFO",
            "log_to_file": True,
            "log_directory": str(tmp_path),
            "log_max_bytes": 1024 * 1024,
            "log_files_per_day": 2,
        }
    )
    try:
        configure_logging(settings)
        logger = logging.getLogger("app.test")
        logger.info("timeline info")
        logger.error("timeline error")
        for handler in logging.getLogger().handlers:
            handler.close()
    finally:
        # 该测试会替换全局 Root Handler；恢复真实配置，避免影响后续 HTTP 测试。
        configure_logging(original_settings)

    today = datetime.now(UTC).date().isoformat()
    app_text = (tmp_path / "app" / f"{today}.log").read_text(encoding="utf-8")
    error_text = (tmp_path / "error" / f"{today}.log").read_text(encoding="utf-8")
    assert "timeline info" in app_text
    assert "timeline error" in app_text
    assert "timeline info" not in error_text
    assert "timeline error" in error_text


async def test_http_response_exposes_generated_request_id(client: AsyncClient) -> None:
    """客户端拿到的 X-Request-ID 可用于在线上日志平台定位同一次请求。"""

    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
