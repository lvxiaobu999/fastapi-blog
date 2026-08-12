"""管理员后台用户接口。

本模块只暴露管理员需要的用户管理契约；公开注册和用户本人资料接口仍由
``api_users.py`` 负责。每个端点都依赖 ``AdminUser``，页面隐藏不是权限边界。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.db.redis import get_redis
from app.db.session import get_db
from app.dependencies.auth import AdminUser
from app.schemas.api import ApiSuccess
from app.schemas.user import AdminUserCreate, AdminUserUpdate, UserResponse
from app.services import refresh_sessions as refresh_session_service
from app.services import users as user_service
from app.services.images import delete_profile_image

# ==================== Router 入口导读 ====================
# admin.js 在后台用户管理页加载、创建、编辑或删除用户时进入本 Router。
# 每个端点都要求 AdminUser：先认证用户，再确认 is_admin=True。这里负责管理员专属的
# HTTP 权限和状态码，用户查询、写入和冲突判断仍复用 services/users.py。
router = APIRouter(prefix="/api/admin/users", tags=["admin"], responses=API_ERROR_RESPONSES)
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


async def _get_user_or_404(session: AsyncSession, user_id: int):
    """查询后台操作目标，不存在时保持标准 404 语义。"""

    user = await user_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.get("", response_model=ApiSuccess[list[UserResponse]])
async def list_users(
    request: Request,
    session: DbSession,
    _admin: AdminUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ApiSuccess[list[UserResponse]]:
    """仅管理员分页读取用户列表。"""

    users = await user_service.list_users(session, offset=offset, limit=limit)
    return success_response(request, [UserResponse.model_validate(user) for user in users])


@router.post("", response_model=ApiSuccess[UserResponse], status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request, data: AdminUserCreate, session: DbSession, _admin: AdminUser
) -> ApiSuccess[UserResponse]:
    """由管理员创建用户，并显式决定初始角色。"""

    try:
        user = await user_service.create_admin_managed_user(session, data)
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username or email already exists"
        ) from exc
    return success_response(request, UserResponse.model_validate(user))


@router.patch("/{user_id}", response_model=ApiSuccess[UserResponse])
async def update_user(
    request: Request,
    user_id: int,
    data: AdminUserUpdate,
    session: DbSession,
    admin: AdminUser,
) -> ApiSuccess[UserResponse]:
    """由管理员更新资料和角色；禁止撤销自己的管理员身份。"""

    user = await _get_user_or_404(session, user_id)
    if user.id == admin.id and data.is_admin is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot remove your own admin role"
        )
    try:
        user = await user_service.update_admin_managed_user(session, user, data)
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username or email already exists"
        ) from exc
    return success_response(request, UserResponse.model_validate(user))


@router.delete("/{user_id}", response_model=ApiSuccess[None])
async def delete_user(
    request: Request, user_id: int, session: DbSession, redis: RedisClient, admin: AdminUser
) -> ApiSuccess[None]:
    """由管理员删除用户；禁止删除当前登录的管理员自身。"""

    user = await _get_user_or_404(session, user_id)
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account from admin",
        )
    # Redis 撤销失败时保留数据库用户，比“用户已删除但会话清理失败”更容易安全重试。
    avatar_filename = user.image_file
    await refresh_session_service.revoke_user_refresh_sessions(redis, user_id)
    await user_service.delete_user(session, user)
    await delete_profile_image(avatar_filename)
    return success_response(request, None)
