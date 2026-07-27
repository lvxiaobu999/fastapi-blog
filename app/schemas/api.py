"""API 成功响应的公共契约。

所有业务 API 使用同一层响应信封，同时通过泛型 ``data`` 保留具体资源类型，便于
FastAPI 正确生成 OpenAPI 文档。异常响应由 ``app.exception_handlers`` 单独定义。
"""

from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

DataT = TypeVar("DataT")


class ResponseMeta(BaseModel):
    """一次 API 响应的追踪信息。"""

    model_config = ConfigDict(populate_by_name=True)

    # requestId 与 X-Request-ID 响应头相同，可用于从客户端问题定位到服务端日志。
    request_id: UUID = Field(alias="requestId")
    # 使用带时区 UTC 时间，避免部署到不同时区后产生歧义。
    timestamp: datetime

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        """输出明确的 ``+00:00`` UTC 偏移，与项目公开响应示例保持一致。"""

        return value.isoformat()


class ApiSuccess(BaseModel, Generic[DataT]):
    """统一成功响应；泛型参数描述 ``data`` 中实际业务数据的类型。"""

    success: Literal[True] = True
    code: Literal[0] = 0
    message: str = "ok"
    data: DataT
    meta: ResponseMeta
