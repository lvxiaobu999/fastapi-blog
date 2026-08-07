"""页面与 API 统一异常响应测试。"""

import json

import pytest
from fastapi import Request
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.exception_handlers import (
    http_exception_handler,
    unexpected_exception_handler,
    redis_exception_handler,
)
from app.main import app

pytestmark = pytest.mark.anyio


def _request(path: str) -> Request:
    """构造绑定真实应用的最小请求，使错误模板可以正常反向生成静态资源地址。"""

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("test", 123),
            "server": ("test", 80),
            "root_path": "",
            "app": app,
        }
    )


async def test_unknown_api_returns_unified_json(client: AsyncClient) -> None:
    """不存在的 API 也必须遵守统一错误契约，而不是返回默认 detail。"""

    response = await client.get("/api/not-found")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["code"] == 40401
    assert body["message"] == "Not Found"
    assert body["data"] is None
    assert body["errors"] is None
    assert body["meta"]["requestId"] == response.headers["X-Request-ID"]
    assert body["meta"]["timestamp"].endswith("+00:00")


async def test_openapi_documents_unified_api_failure() -> None:
    """业务 API 的 Swagger 文档应引用与运行时一致的失败响应 Schema。"""

    operation = app.openapi()["paths"]["/api/posts"]["post"]

    assert operation["responses"]["401"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiFailure"
    }
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ApiFailure"
    }


@pytest.mark.parametrize(
    ("status_code", "expected_text"),
    [(401, "需要登录"), (403, "没有访问权限"), (404, "页面不存在")],
)
async def test_page_http_errors_use_special_templates(
    status_code: int,
    expected_text: str,
) -> None:
    """页面的认证、授权和不存在异常分别渲染对应 HTML。"""

    response = await http_exception_handler(
        _request("/private"),
        StarletteHTTPException(status_code=status_code, detail="test"),
    )

    assert response.status_code == status_code
    assert expected_text.encode() in response.body


async def test_unexpected_errors_hide_details_from_api_and_page() -> None:
    """未知异常记录在服务端，但 API 和页面都不能泄露内部错误内容。"""

    api_response = await unexpected_exception_handler(
        _request("/api/crash"), RuntimeError("database password leaked")
    )
    page_response = await unexpected_exception_handler(
        _request("/crash"), RuntimeError("database password leaked")
    )
    api_body = json.loads(api_response.body)

    assert api_response.status_code == 500
    assert api_body["success"] is False
    assert api_body["code"] == 50001
    assert api_body["data"] is None
    assert api_body["errors"] is None
    assert "database password leaked" not in api_response.body.decode()
    assert page_response.status_code == 500
    assert "服务器暂时出现异常".encode() in page_response.body
    assert "database password leaked".encode() not in page_response.body


async def test_redis_errors_return_safe_503_response() -> None:
    """Redis 连接详情只写服务端日志，认证 API 对客户端返回稳定 503。"""

    response = await redis_exception_handler(
        _request("/api/auth/token"), RedisConnectionError("redis://secret@internal:6379")
    )
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["code"] == 50301
    assert body["message"] == "Authentication service temporarily unavailable"
    assert "secret" not in response.body.decode()
