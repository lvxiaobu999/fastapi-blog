"""OAuth2 Password Flow 登录接口；负责 HTTP 表单解析和认证失败响应。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.auth import TokenResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/token", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: DbSession
) -> TokenResponse:
    """接收 OAuth2 Password Flow 表单并签发短期 Bearer Token。

    OAuth2 规范固定字段名为 ``username`` 和 ``password``，即使业务登录名实际是用户
    名称也不能改成 JSON 字段。Router 只负责表单和 HTTP 状态码，密码查询与 JWT 细节
    委托给 Service。
    """

    user = await auth_service.authenticate_user(session, form.username, form.password)
    if user is None:
        # 不区分“用户不存在”和“密码错误”，避免泄露账号存在性。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=auth_service.create_access_token(user.id))
