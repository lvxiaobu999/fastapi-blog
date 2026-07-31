"""文章互动和当前用户活动历史 HTTP 接口。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.db.session import get_db
from app.dependencies.auth import CurrentUser, OptionalCurrentUser
from app.schemas.api import ApiSuccess
from app.schemas.post import PostInteractionState, UserActivityItem, UserCommentActivityItem
from app.services import post_activities as activity_service
from app.services import posts as post_service

router = APIRouter(tags=["post activities"], responses=API_ERROR_RESPONSES)
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _post_or_404(session: AsyncSession, post_id: int):
    """读取文章或转换为统一的 404。"""

    post = await post_service.get_post(session, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@router.get("/api/posts/{post_id}/interaction", response_model=ApiSuccess[PostInteractionState])
async def get_interaction(request: Request, post_id: int, session: DbSession, user: OptionalCurrentUser) -> ApiSuccess[PostInteractionState]:
    """读取文章计数和当前用户状态，不要求登录。"""

    post = await _post_or_404(session, post_id)
    return success_response(request, await activity_service.interaction_state(session, post, user.id if user else None))


@router.post("/api/posts/{post_id}/view", response_model=ApiSuccess[PostInteractionState])
async def record_view(request: Request, post_id: int, session: DbSession, user: OptionalCurrentUser) -> ApiSuccess[PostInteractionState]:
    """记录一次详情页浏览；登录用户同时生成或更新足迹。"""

    post = await _post_or_404(session, post_id)
    return success_response(request, await activity_service.record_view(session, post, user.id if user else None))


@router.post("/api/posts/{post_id}/like", response_model=ApiSuccess[PostInteractionState])
async def toggle_like(request: Request, post_id: int, session: DbSession, user: CurrentUser) -> ApiSuccess[PostInteractionState]:
    """登录用户切换文章点赞。"""

    post = await _post_or_404(session, post_id)
    return success_response(request, await activity_service.toggle_like(session, post, user.id))


@router.post("/api/posts/{post_id}/favorite", response_model=ApiSuccess[PostInteractionState])
async def toggle_favorite(request: Request, post_id: int, session: DbSession, user: CurrentUser) -> ApiSuccess[PostInteractionState]:
    """登录用户切换文章收藏。"""

    post = await _post_or_404(session, post_id)
    return success_response(request, await activity_service.toggle_favorite(session, post, user.id))


@router.get("/api/me/activities/posts", response_model=ApiSuccess[list[UserActivityItem]])
async def my_post_activities(request: Request, session: DbSession, user: CurrentUser, kind: Annotated[Literal["likes", "favorites", "views"], Query()]) -> ApiSuccess[list[UserActivityItem]]:
    """查询当前用户点赞、收藏或浏览过的文章。"""

    return success_response(request, await activity_service.list_post_activity(session, user.id, kind))


@router.get("/api/me/activities/comments", response_model=ApiSuccess[list[UserCommentActivityItem]])
async def my_comment_activities(request: Request, session: DbSession, user: CurrentUser) -> ApiSuccess[list[UserCommentActivityItem]]:
    """查询当前用户发表过的评论。"""

    return success_response(request, await activity_service.list_comment_activity(session, user.id))
