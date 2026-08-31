"""博客分类的请求与响应契约。

公开页面只读取 ``CategoryResponse``；管理员分类管理使用独立的创建和更新 Schema，避免
把数据库字段校验、部分更新语义和响应字段混在一起。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip_text(value: str) -> str:
    """去掉分类名称或 slug 两侧空白，避免看似不同的重复分类。"""

    return value.strip()


class CategoryCreate(BaseModel):
    """管理员创建分类时提交的名称、稳定标识和展示顺序。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=50)
    slug: str = Field(
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$",
        description="只允许小写字母、数字、连字符或下划线，供 URL 筛选使用",
    )
    sort_order: int = Field(default=0, ge=0, le=2_147_483_647)

    @field_validator("name", "slug", mode="before")
    @classmethod
    def strip_values(cls, value: str) -> str:
        """在长度和格式校验前统一去除首尾空格。"""

        return _strip_text(value) if isinstance(value, str) else value

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str) -> str:
        """slug 不区分大小写，统一保存为小写，避免 URL 出现两套入口。"""

        return value.lower()


class CategoryUpdate(BaseModel):
    """管理员部分更新分类；未提交的字段保持原值。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=50)
    slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$",
        description="只允许小写字母、数字、连字符或下划线，供 URL 筛选使用",
    )
    sort_order: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @field_validator("name", "slug", mode="before")
    @classmethod
    def strip_values(cls, value: str | None) -> str | None:
        """允许省略字段，但不允许用首尾空格制造隐藏差异。"""

        return None if value is None else (_strip_text(value) if isinstance(value, str) else value)

    @field_validator("slug")
    @classmethod
    def normalize_slug(cls, value: str | None) -> str | None:
        """更新 slug 时同样统一为小写。"""

        return None if value is None else value.lower()


class CategoryResponse(BaseModel):
    """可安全返回给页面和 API 客户端的分类数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str = Field(min_length=1, max_length=50)
    slug: str = Field(min_length=1, max_length=50)
    sort_order: int
