from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.category import CategoryResponse
from app.schemas.user import UserPublic


class PostBase(BaseModel):
    """创建请求与响应共同包含的帖子基础字段。"""

    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class PostCreate(PostBase):
    """Service 内部创建帖子时使用的完整数据。"""

    user_id: int
    # 旧 Service 调用未传分类时落到“其它”；HTTP 创建请求仍要求明确选择分类。
    category_id: int | None = Field(default=None, gt=0)


class PostCreateRequest(PostBase):
    """发帖 HTTP 请求；作者必须从 JWT 获取，客户端不能指定 user_id。"""

    category_id: int = Field(gt=0)


class PostUpdate(BaseModel):
    """帖子部分更新参数；没有传入的字段保持原值。"""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)
    category_id: int | None = Field(default=None, gt=0)

    @field_validator("category_id")
    @classmethod
    def category_cannot_be_null(cls, value: int | None) -> int | None:
        """PATCH 允许省略分类，但不允许把已有帖子的分类显式清空。"""

        if value is None:
            return value
        return value


class PostQueryParams(BaseModel):
    """帖子列表查询参数；keyword 为空时不进行关键词过滤。"""

    keyword: str | None = Field(default=None, min_length=1, max_length=100)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class PostResponse(PostBase):
    """帖子公开响应，包含完整的作者公开信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    user_id: int
    author: UserPublic
    category_id: int
    category: CategoryResponse
