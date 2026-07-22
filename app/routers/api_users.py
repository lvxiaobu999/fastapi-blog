"""用户 CRUD HTTP 接口；当前阶段不包含认证和权限判断。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas import UserCreate, UserResponse, UserUpdate
from app.services import users as user_service

router = APIRouter(prefix="/api/users", tags=["users"])
DbSession = Annotated[Session, Depends(get_db)]


def _get_user_or_404(session: Session, user_id: int):
    """复用用户查询逻辑，并将不存在的资源转换为 HTTP 404。"""

    user = user_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(data: UserCreate, session: DbSession):
    """创建用户；用户名或邮箱冲突时返回 409。"""

    try:
        return user_service.create_user(session, data)
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from exc


@router.get("", response_model=list[UserResponse])
def list_users(
    session: DbSession,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
):
    """分页获取用户列表，单次最多返回 100 条。"""

    return user_service.list_users(session, offset=offset, limit=limit)


@router.get("/{user_id}", response_model=UserResponse)
def get_user(user_id: int, session: DbSession):
    """获取单个用户的公开信息。"""

    return _get_user_or_404(session, user_id)


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, data: UserUpdate, session: DbSession):
    """部分更新用户；未传入的字段保持不变。"""

    user = _get_user_or_404(session, user_id)
    try:
        return user_service.update_user(session, user, data)
    except user_service.UserAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already exists",
        ) from exc


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, session: DbSession) -> Response:
    """删除用户，成功时返回没有响应体的 204。"""

    user = _get_user_or_404(session, user_id)
    user_service.delete_user(session, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
