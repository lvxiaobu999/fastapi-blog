"""博客分类的公开响应契约。"""

from pydantic import BaseModel, ConfigDict, Field


class CategoryResponse(BaseModel):
    """可安全返回给页面和 API 客户端的分类数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str = Field(min_length=1, max_length=50)
    slug: str = Field(min_length=1, max_length=50)
    sort_order: int
