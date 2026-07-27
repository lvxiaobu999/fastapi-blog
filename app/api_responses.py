"""构造统一 API 成功响应。

本模块只组装 HTTP 响应契约，不处理业务逻辑、数据库事务或异常转换。
"""

from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi import Request

from app.schemas.api import ApiSuccess, ResponseMeta

DataT = TypeVar("DataT")


def request_id(request: Request) -> UUID:
    """读取当前请求 ID；直接调用处理函数时使用新 UUID 作为安全回退。"""

    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, UUID) else uuid4()


def success_response(
    request: Request,
    data: DataT,
    *,
    message: str = "ok",
) -> ApiSuccess[DataT]:
    """用业务数据、请求 ID 和当前 UTC 时间构造统一成功响应。"""

    return ApiSuccess[DataT](
        message=message,
        data=data,
        meta=ResponseMeta(
            request_id=request_id(request),
            timestamp=datetime.now(UTC),
        ),
    )
