from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserResponse


class PostBase(BaseModel):
    """创建请求与响应共同包含的帖子基础字段。"""

    title: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1)


class PostCreate(PostBase):
    """创建帖子时需要指定作者用户 ID。"""

    user_id: int


class PostUpdate(BaseModel):
    """帖子部分更新参数；没有传入的字段保持原值。"""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)


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
    author: UserResponse
