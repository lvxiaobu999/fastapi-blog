"""用户 CRUD HTTP 接口；当前阶段不包含认证和权限判断。"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.schemas import UserCreate, UserResponse, UserUpdate
from app.schemas.api import ApiSuccess
from app.services import users as user_service
from app.services.images import InvalidImageError, save_profile_image

router = APIRouter(prefix="/api/users", tags=["users"], responses=API_ERROR_RESPONSES)
DbSession = Annotated[AsyncSession, Depends(get_db)]


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


@router.get("", response_model=ApiSuccess[list[UserResponse]])
async def list_users(
    request: Request,
    session: DbSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> ApiSuccess[list[UserResponse]]:
    """分页获取用户列表，单次最多返回 100 条。"""

    users = await user_service.list_users(session, offset=offset, limit=limit)
    data = [UserResponse.model_validate(user) for user in users]
    return success_response(request, data)


@router.get("/{user_id}", response_model=ApiSuccess[UserResponse])
async def get_user(request: Request, user_id: int, session: DbSession) -> ApiSuccess[UserResponse]:
    """获取单个用户的公开信息。"""

    user = await _get_user_or_404(session, user_id)
    return success_response(request, UserResponse.model_validate(user))


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
    current_user: CurrentUser,
) -> ApiSuccess[None]:
    """用户本人或管理员删除用户，并返回统一成功响应。"""

    user = await _get_user_or_404(session, user_id)
    if current_user.id != user.id and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
    await user_service.delete_user(session, user)
    return success_response(request, None)


@router.post("/me/avatar", response_model=ApiSuccess[UserResponse])
async def upload_avatar(
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
    avatar: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP; max 5 MB")],
) -> ApiSuccess[UserResponse]:
    """校验并保存当前用户头像，不允许通过路径参数替其他用户上传。"""

    try:
        filename = await save_profile_image(avatar)
    except InvalidImageError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await avatar.close()
    user = await user_service.set_profile_image(session, current_user, filename)
    return success_response(request, UserResponse.model_validate(user))
