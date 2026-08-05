from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.category import CategoryResponse
from app.schemas.user import UserPublic


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
    # 旧 Service 调用未传分类时落到“其它”；HTTP 创建请求仍要求明确选择分类。
    category_id: int | None = Field(default=None, gt=0)


class PostCreateRequest(PostBase):
    """发帖 HTTP 请求；作者必须从 JWT 获取，客户端不能指定 user_id。"""

    category_id: int = Field(gt=0)


class PostUpdate(BaseModel):
    """帖子部分更新参数；没有传入的字段保持原值。"""

    title: str | None = Field(default=None, min_length=1, max_length=100)
    summary: str | None = Field(default=None, max_length=300)
    cover_image_url: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, min_length=1)
    category_id: int | None = Field(default=None, gt=0)
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

    @field_validator("category_id")
    @classmethod
    def category_cannot_be_null(cls, value: int | None) -> int | None:
        """PATCH 允许省略分类，但不允许把已有帖子的分类显式清空。"""

        if value is None:
            raise ValueError("category_id cannot be null")
        return value


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
    category_id: int
    category: CategoryResponse
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
