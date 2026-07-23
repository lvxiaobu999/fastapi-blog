"""帖子 HTTP 接口；当前尚未接入具体端点。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.post import PostCreate, PostQueryParams, PostResponse, PostUpdate
from app.services import posts as post_service

DbSession = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix="/api/posts", tags=["posts"])


async def _get_post_or_404(session: AsyncSession, post_id: int):
    """复用帖子查询逻辑，并将不存在的资源转换为 HTTP 404。"""

    post = await post_service.get_post(session, post_id)

    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return post


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(data: PostCreate, session: DbSession):
    """创建帖子并返回包含数据库生成字段的完整响应。"""

    return await post_service.create_post(session, data)


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
async def update_post(post_id: int, data: PostUpdate, session: DbSession):
    """部分更新用户；未传入的字段保持不变。"""

    post = await _get_post_or_404(session, post_id)

    return await post_service.update_post(session, post, data)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: int, session: DbSession) -> Response:
    post = await _get_post_or_404(session, post_id)
    await post_service.delete_post(session, post)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
