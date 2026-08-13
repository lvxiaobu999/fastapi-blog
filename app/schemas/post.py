from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.category import CategoryResponse
from app.schemas.user import UserPublic

CategoryId = Annotated[int, Field(gt=0)]


def _unique_category_ids(values: list[int]) -> list[int]:
    """按提交顺序去重，避免关联表联合主键收到重复关系。"""

    return list(dict.fromkeys(values))


class PostBase(BaseModel):
    """创建请求与响应共同包含的帖子基础字段。"""

    title: str = Field(min_length=1, max_length=100)
    summary: str | None = Field(default=None, max_length=300)
    cover_image_url: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=1)
    is_published: bool = True

    @field_validator("cover_image_url")
    @classmethod
    def cover_must_use_uploaded_media(cls, value: str | None) -> str | None:
        """横图只能引用本站图片上传接口返回的路径，不能保存任意外站地址。"""

        if value in (None, ""):
            return None
        if not value.startswith("/media/post_images/"):
            raise ValueError("cover_image_url must be an uploaded post image")
        return value


class PostCreate(PostBase):
    """Service 内部创建帖子时使用的完整数据。"""

    user_id: int
    category_ids: list[CategoryId] | None = None
    # 仅兼容仓库内尚未迁移的可信调用；公开 HTTP 契约只接受 category_ids。
    category_id: CategoryId | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_category_input(self) -> "PostCreate":
        """拒绝同时使用新旧分类参数，并对多分类 ID 去重。"""

        if self.category_ids is not None and self.category_id is not None:
            raise ValueError("category_ids and category_id cannot be used together")
        if self.category_ids is not None:
            if not self.category_ids:
                raise ValueError("at least one category is required")
            self.category_ids = _unique_category_ids(self.category_ids)
        return self


class PostCreateRequest(PostBase):
    """发帖 HTTP 请求；作者必须从 JWT 获取，客户端不能指定 user_id。"""

    category_ids: list[CategoryId] = Field(min_length=1)

    @field_validator("category_ids")
    @classmethod
    def categories_must_be_unique(cls, value: list[int]) -> list[int]:
        """接受重复选择但只保留一次，保持请求幂等且避免数据库唯一约束错误。"""

        return _unique_category_ids(value)


class PostUpdate(BaseModel):
    """帖子部分更新参数；没有传入的字段保持原值。"""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    summary: str | None = Field(default=None, max_length=300)
    cover_image_url: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, min_length=1)
    category_ids: list[CategoryId] | None = None
    is_published: bool | None = None

    @field_validator("cover_image_url")
    @classmethod
    def cover_must_use_uploaded_media(cls, value: str | None) -> str | None:
        """允许传 null 移除横图；新地址必须来自站内帖子图片目录。"""

        if value in (None, ""):
            return None
        if not value.startswith("/media/post_images/"):
            raise ValueError("cover_image_url must be an uploaded post image")
        return value

    @field_validator("category_ids")
    @classmethod
    def categories_cannot_be_empty(cls, value: list[int] | None) -> list[int]:
        """PATCH 允许省略分类，但显式提交时必须至少保留一个分类。"""

        if not value:
            raise ValueError("at least one category is required")
        return _unique_category_ids(value)


class PostQueryParams(BaseModel):
    """帖子列表查询参数；关键词和分类均为空时返回全部帖子。"""

    keyword: str | None = Field(default=None, min_length=1, max_length=100)
    category: str | None = Field(default=None, min_length=1, max_length=50)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class PostTitleSearchParams(BaseModel):
    """导航搜索弹窗的查询参数，只允许小批量返回标题候选项。"""

    keyword: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=8, ge=1, le=20)


class PostTitleSearchResult(BaseModel):
    """搜索弹窗使用的轻量结果，不传输文章正文和作者等无关字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str = Field(min_length=1, max_length=100)


class PostResponse(PostBase):
    """帖子公开响应，包含完整的作者公开信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    user_id: int
    author: UserPublic
    category_ids: list[int] = Field(min_length=1)
    categories: list[CategoryResponse] = Field(min_length=1)
    view_count: int = Field(ge=0)


class PostInteractionState(BaseModel):
    """文章详情操作区状态；未登录用户的两个布尔值均为 ``False``。"""

    # 下面三个 count 是页面要显示的全站统计值；ge=0 禁止输出负数。
    view_count: int = Field(ge=0)  # 文章累计被打开多少次。
    like_count: int = Field(ge=0)  # post_likes 表中属于该文章的记录数。
    favorite_count: int = Field(ge=0)  # post_favorites 表中的对应记录数。
    # 下面两个布尔值只描述“当前登录用户”。游客没有用户身份，因此都是 False。
    liked: bool  # 当前用户是否已经点赞，用于决定按钮是否高亮。
    favorited: bool  # 当前用户是否已经收藏，用于决定按钮是否高亮。


class UserActivityItem(BaseModel):
    """个人活动列表中的文章摘要。"""

    id: int  # 文章 ID，前端用它生成 /posts/{id} 链接。
    title: str  # 文章标题。
    created_at: datetime  # 文章本身的发布时间。
    activity_at: datetime  # 当前用户点赞、收藏或最近浏览这篇文章的时间。
    view_count: int = Field(ge=0)  # 文章当前累计浏览量。


class UserCommentActivityItem(BaseModel):
    """个人评论历史，包含跳回原文章所需的最小信息。"""

    id: int  # 评论 ID。
    content: str  # 用户当时发表的评论正文。
    created_at: datetime  # 评论发表时间。
    post_id: int  # 所属文章 ID，用于点击后跳回文章详情。
    post_title: str  # 所属文章标题，让列表无需再发一次请求就能展示上下文。
