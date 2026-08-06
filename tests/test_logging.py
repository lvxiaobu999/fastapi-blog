"""结构化日志与 HTTP 请求追踪测试。"""

import json
import logging
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.logging import JsonFormatter, RequestContextFilter, bind_request_id, reset_request_id

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


async def test_http_response_exposes_generated_request_id(client: AsyncClient) -> None:
    """客户端拿到的 X-Request-ID 可用于在线上日志平台定位同一次请求。"""

    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
