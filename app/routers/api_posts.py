"""帖子 HTTP 接口；当前尚未接入具体端点。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import AdminUser
from app.schemas.post import (
    PostCreate,
    PostCreateRequest,
    PostQueryParams,
    PostResponse,
    PostUpdate,
)
from app.schemas.upload import ImageUploadResponse
from app.services.images import InvalidImageError, save_post_image
from app.services import posts as post_service

DbSession = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix="/api/posts", tags=["posts"])


@router.post(
    "/images",
    response_model=ImageUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_post_image(
    _current_user: AdminUser,
    image: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP; max 5 MB")],
) -> ImageUploadResponse:
    """为富文本编辑器保存图片；只有管理员能够写入帖子媒体目录。"""

    try:
        url = await save_post_image(image)
    except InvalidImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await image.close()
    return ImageUploadResponse(url=url)


async def _get_post_or_404(session: AsyncSession, post_id: int):
    """复用帖子查询逻辑，并将不存在的资源转换为 HTTP 404。"""

    post = await post_service.get_post(session, post_id)

    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return post


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(data: PostCreateRequest, session: DbSession, current_user: AdminUser):
    """由已认证管理员创建帖子；作者 ID 始终取自 JWT 对应用户。

    ``PostCreateRequest`` 故意不包含 ``user_id``。如果直接信任前端传来的作者 ID，
    登录用户就可以冒充其他用户发帖；这里从 ``AdminUser`` 依赖得到经过 JWT 和数据库
    校验的身份，再由服务层保存该 ID。
    """

    return await post_service.create_post(
        session, PostCreate(**data.model_dump(), user_id=current_user.id)
    )


@router.get("", response_model=list[PostResponse])
async def list_posts(
    session: DbSession,
    params: Annotated[PostQueryParams, Query()],
):
    """分页获取帖子列表，并按关键词模糊搜索标题和正文。"""

    return await post_service.list_posts(session, params)


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(post_id: int, session: DbSession):
    """获取单个用户的公开信息。"""

    return await _get_post_or_404(session, post_id)


@router.patch("/{post_id}", response_model=PostResponse)
async def update_post(post_id: int, data: PostUpdate, session: DbSession, _current_user: AdminUser):
    """由已认证管理员部分更新帖子；未传入的字段保持不变。"""

    post = await _get_post_or_404(session, post_id)

    return await post_service.update_post(session, post, data)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: int, session: DbSession, _current_user: AdminUser) -> Response:
    """由已认证管理员删除帖子。"""

    post = await _get_post_or_404(session, post_id)
    await post_service.delete_post(session, post)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
