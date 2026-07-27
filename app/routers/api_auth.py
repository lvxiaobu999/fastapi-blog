"""OAuth2 Password Flow 登录接口；负责 HTTP 表单解析和认证失败响应。"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_settings
from app.db.session import get_db
from app.schemas.auth import TokenResponse
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("/token", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], session: DbSession
) -> Response:
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
    refresh_token = await auth_service.create_refresh_session(session, user.id)
    result = TokenResponse(access_token=auth_service.create_access_token(user.id))
    response = Response(content=result.model_dump_json(), media_type="application/json")
    settings = get_settings()
    response.set_cookie(
        "refresh_token",
        refresh_token,
        # httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_minutes * 60,
        path="/api",
    )
    return response


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    session: DbSession, refresh_token: Annotated[str | None, Cookie()] = None
) -> Response:
    """使用 HttpOnly Cookie 换取新 Access Token，并轮换 Refresh Token。"""

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required")
    rotated = await auth_service.rotate_refresh_session(session, refresh_token)
    if rotated is None:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    user_id, new_refresh = rotated
    result = TokenResponse(access_token=auth_service.create_access_token(user_id))
    response = Response(content=result.model_dump_json(), media_type="application/json")
    settings = get_settings()
    response.set_cookie(
        "refresh_token",
        new_refresh,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_minutes * 60,
        path="/api",
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    """清除浏览器 Refresh Cookie；前端同时删除 Access Token。"""

    response.delete_cookie("refresh_token", path="/api")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
