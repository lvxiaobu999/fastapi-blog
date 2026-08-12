"""构造统一 API 成功响应。

本模块只组装 HTTP 响应契约，不处理业务逻辑、数据库事务或异常转换。
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request

from app.schemas.api import ApiErrorItem, ApiFailure, ApiSuccess, ResponseMeta

# APIRouter 复用这份声明，使 Swagger 展示的失败契约与全局异常处理器一致。
API_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ApiFailure, "description": "Bad request"},
    401: {"model": ApiFailure, "description": "Authentication required"},
    403: {"model": ApiFailure, "description": "Permission denied"},
    404: {"model": ApiFailure, "description": "Resource not found"},
    409: {"model": ApiFailure, "description": "Resource conflict"},
    422: {"model": ApiFailure, "description": "Request validation failed"},
    500: {"model": ApiFailure, "description": "Internal server error"},
}


def request_id(request: Request) -> UUID:
    """读取当前请求 ID；直接调用处理函数时使用新 UUID 作为安全回退。"""

    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, UUID) else uuid4()


def response_meta(request: Request) -> ResponseMeta:
    """构造成功和失败响应共用的请求追踪元数据。"""

    return ResponseMeta(
        request_id=request_id(request),
        timestamp=datetime.now(UTC),
    )


def success_response[DataT](
    request: Request,
    data: DataT,
    *,
    message: str = "ok",
) -> ApiSuccess[DataT]:
    """用业务数据、请求 ID 和当前 UTC 时间构造统一成功响应。"""

    return ApiSuccess[DataT](
        message=message,
        data=data,
        meta=response_meta(request),
    )


def failure_response(
    request: Request,
    *,
    code: int,
    message: str,
    errors: list[ApiErrorItem] | None = None,
) -> ApiFailure:
    """构造统一失败响应；HTTP 状态码仍由异常处理器单独设置。"""

    return ApiFailure(
        code=code,
        message=message,
        meta=response_meta(request),
        errors=errors,
    )
