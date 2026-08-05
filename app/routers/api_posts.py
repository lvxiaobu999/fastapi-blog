"""帖子 HTTP 接口；当前尚未接入具体端点。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.db.session import get_db
from app.dependencies.auth import AdminUser
from app.schemas.api import ApiSuccess
from app.schemas.post import (
    PostCreate,
    PostCreateRequest,
    PostQueryParams,
    PostResponse,
    PostTitleSearchParams,
    PostTitleSearchResult,
    PostUpdate,
)
from app.schemas.upload import ImageUploadResponse
from app.services.images import InvalidImageError, save_post_image
from app.services import posts as post_service

DbSession = Annotated[AsyncSession, Depends(get_db)]

# ==================== Router 入口导读 ====================
# search.js 调用标题搜索，admin.js 调用后台文章列表，posts.js 调用文章发布、编辑和图片上传。
# GET 查询公开可用；写文章和上传图片要求 AdminUser。Router 校验 HTTP 参数和权限，
# 文章/分类查询写入交给 services/posts.py，图片文件交给 services/images.py。
router = APIRouter(prefix="/api/posts", tags=["posts"], responses=API_ERROR_RESPONSES)


@router.post(
    "/images",
    response_model=ApiSuccess[ImageUploadResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_post_image(
    request: Request,
    _current_user: AdminUser,
    image: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP; max 5 MB")],
) -> ApiSuccess[ImageUploadResponse]:
    """为富文本编辑器保存图片；只有管理员能够写入帖子媒体目录。"""

    try:
        url = await save_post_image(image)
    except InvalidImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await image.close()
    return success_response(request, ImageUploadResponse(url=url))


async def _get_post_or_404(session: AsyncSession, post_id: int):
    """复用帖子查询逻辑，并将不存在的资源转换为 HTTP 404。"""

    post = await post_service.get_post(session, post_id)

    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return post


@router.post("", response_model=ApiSuccess[PostResponse], status_code=status.HTTP_201_CREATED)
async def create_post(
    request: Request,
    data: PostCreateRequest,
    session: DbSession,
    current_user: AdminUser,
) -> ApiSuccess[PostResponse]:
    """由已认证管理员创建帖子；作者 ID 始终取自 JWT 对应用户。

    ``PostCreateRequest`` 故意不包含 ``user_id``。如果直接信任前端传来的作者 ID，
    登录用户就可以冒充其他用户发帖；这里从 ``AdminUser`` 依赖得到经过 JWT 和数据库
    校验的身份，再由服务层保存该 ID。
    """

    try:
        post = await post_service.create_post(
            session, PostCreate(**data.model_dump(), user_id=current_user.id)
        )
    except post_service.PostCategoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Category not found"
        ) from exc
    # Service 返回 SQLAlchemy Post；Router 在公开响应边界转换为 PostResponse，既过滤
    # ORM 内部字段，也让 ApiSuccess 的泛型参数与声明的返回类型完全一致。
    return success_response(request, PostResponse.model_validate(post))


@router.get("", response_model=ApiSuccess[list[PostResponse]])
async def list_posts(
    request: Request,
    session: DbSession,
    params: Annotated[PostQueryParams, Query()],
) -> ApiSuccess[list[PostResponse]]:
    """分页获取帖子列表，并按关键词或分类 slug 筛选。"""

    posts = await post_service.list_posts(session, params)
    data = [PostResponse.model_validate(post) for post in posts]
    return success_response(request, data)


@router.get("/admin", response_model=ApiSuccess[list[PostResponse]])
async def list_admin_posts(
    request: Request,
    session: DbSession,
    _current_user: AdminUser,
    params: Annotated[PostQueryParams, Query()],
) -> ApiSuccess[list[PostResponse]]:
    """为后台帖子管理返回全部文章，包括已经下架的文章。"""

    posts = await post_service.list_posts(session, params, include_unpublished=True)
    return success_response(request, [PostResponse.model_validate(post) for post in posts])


@router.get("/search", response_model=ApiSuccess[list[PostTitleSearchResult]])
async def search_post_titles(
    request: Request,
    session: DbSession,
    params: Annotated[PostTitleSearchParams, Query()],
) -> ApiSuccess[list[PostTitleSearchResult]]:
    """为导航搜索弹窗返回标题候选，不传输文章正文。"""

    posts = await post_service.search_post_titles(session, params)
    data = [PostTitleSearchResult.model_validate(post) for post in posts]
    return success_response(request, data)


@router.get("/{post_id}", response_model=ApiSuccess[PostResponse])
async def get_post(request: Request, post_id: int, session: DbSession) -> ApiSuccess[PostResponse]:
    """获取单个用户的公开信息。"""

    post = await post_service.get_post(session, post_id, include_unpublished=False)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return success_response(request, PostResponse.model_validate(post))


@router.patch("/{post_id}", response_model=ApiSuccess[PostResponse])
async def update_post(
    request: Request,
    post_id: int,
    data: PostUpdate,
    session: DbSession,
    _current_user: AdminUser,
) -> ApiSuccess[PostResponse]:
    """由已认证管理员部分更新帖子；未传入的字段保持不变。"""

    post = await _get_post_or_404(session, post_id)

    try:
        updated = await post_service.update_post(session, post, data)
    except post_service.PostCategoryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Category not found"
        ) from exc
    return success_response(request, PostResponse.model_validate(updated))


@router.delete(
    "/{post_id}",
    response_model=ApiSuccess[None],
    status_code=status.HTTP_200_OK,
)
async def delete_post(
    request: Request,
    post_id: int,
    session: DbSession,
    _current_user: AdminUser,
) -> ApiSuccess[None]:
    """由已认证管理员删除帖子。"""

    post = await _get_post_or_404(session, post_id)
    await post_service.delete_post(session, post)
    return success_response(request, None)
