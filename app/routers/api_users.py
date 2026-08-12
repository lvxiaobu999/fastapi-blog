"""用户 CRUD HTTP 接口；当前阶段不包含认证和权限判断。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.db.redis import get_redis
from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.schemas import UserCreate, UserResponse, UserUpdate
from app.schemas.api import ApiSuccess
from app.schemas.user import UserPublic
from app.services import refresh_sessions as refresh_session_service
from app.services import users as user_service
from app.services.images import InvalidImageError, delete_profile_image, save_profile_image

# ==================== Router 入口导读 ====================
# 注册表单会 POST /api/users；个人资料页 forms.js 会 PATCH 用户资料和即时上传头像；
# 公开资料页或管理逻辑也会读取用户。CurrentUser 依赖先验证 Bearer Token，Router 再检查
# “本人或管理员”权限，持久化工作交给 services/users.py 和 services/images.py。
router = APIRouter(prefix="/api/users", tags=["users"], responses=API_ERROR_RESPONSES)
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


async def _get_user_or_404(session: AsyncSession, user_id: int):
    """复用用户查询逻辑，并将不存在的资源转换为 HTTP 404。"""

    user = await user_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("", response_model=ApiSuccess[UserResponse], status_code=status.HTTP_201_CREATED)
async def create_user(
    request: Request,
    data: UserCreate,
    session: DbSession,
) -> ApiSuccess[UserResponse]:
    """创建用户；用户名或邮箱冲突时返回 409。"""

    try:
        user = await user_service.create_user(session, data)
        return success_response(request, UserResponse.model_validate(user))
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from exc


@router.get("", response_model=ApiSuccess[list[UserPublic]])
async def list_users(
    request: Request,
    session: DbSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ApiSuccess[list[UserPublic]]:
    """分页获取公开用户资料，单次最多返回 100 条且不包含邮箱。"""

    users = await user_service.list_users(session, offset=offset, limit=limit)
    # 列表接口无需认证，必须使用不含邮箱的公开契约，避免被批量收集联系方式。
    data = [UserPublic.model_validate(user) for user in users]
    return success_response(request, data)


@router.get("/{user_id}", response_model=ApiSuccess[UserPublic])
async def get_user(request: Request, user_id: int, session: DbSession) -> ApiSuccess[UserPublic]:
    """获取单个用户的公开信息。"""

    user = await _get_user_or_404(session, user_id)
    return success_response(request, UserPublic.model_validate(user))


@router.patch("/{user_id}", response_model=ApiSuccess[UserResponse])
async def update_user(
    request: Request,
    user_id: int,
    data: UserUpdate,
    session: DbSession,
    current_user: CurrentUser,
) -> ApiSuccess[UserResponse]:
    """用户本人或管理员更新昵称和邮箱；用户名只能由管理员后台维护。"""

    user = await _get_user_or_404(session, user_id)
    if current_user.id != user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    try:
        updated = await user_service.update_user(session, user, data)
        return success_response(request, UserResponse.model_validate(updated))
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from exc


@router.delete(
    "/{user_id}",
    response_model=ApiSuccess[None],
    status_code=status.HTTP_200_OK,
)
async def delete_user(
    request: Request,
    user_id: int,
    session: DbSession,
    redis: RedisClient,
    current_user: CurrentUser,
) -> ApiSuccess[None]:
    """用户本人或管理员删除用户，并返回统一成功响应。"""

    user = await _get_user_or_404(session, user_id)
    if current_user.id != user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    avatar_filename = user.image_file
    await refresh_session_service.revoke_user_refresh_sessions(redis, user_id)
    await user_service.delete_user(session, user)
    await delete_profile_image(avatar_filename)
    return success_response(request, None)


@router.post("/me/avatar", response_model=ApiSuccess[UserResponse])
async def upload_avatar(
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
    avatar: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP; max 5 MB")],
) -> ApiSuccess[UserResponse]:
    """校验并保存当前用户头像，不允许通过路径参数替其他用户上传。"""

    old_filename = current_user.image_file
    try:
        filename = await save_profile_image(avatar)
    except InvalidImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await avatar.close()
    try:
        user = await user_service.set_profile_image(session, current_user, filename)
    except SQLAlchemyError:
        # 新文件先落盘、数据库后提交；提交失败时补偿删除新文件，避免无引用文件累积。
        await delete_profile_image(filename)
        raise
    if old_filename != filename:
        await delete_profile_image(old_filename)
    return success_response(request, UserResponse.model_validate(user))
