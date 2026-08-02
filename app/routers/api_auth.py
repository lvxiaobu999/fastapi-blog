"""OAuth2 Password Flow 登录接口；负责 HTTP 表单解析和认证失败响应。"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.core import get_settings
from app.db.session import get_db
from app.schemas.auth import PasswordChangeRequest, TokenResponse
from app.schemas.api import ApiSuccess
from app.schemas.user import UserResponse
from app.dependencies.auth import CurrentUser
from app.services import auth as auth_service
from app.services import users as user_service

# ==================== Router 入口导读 ====================
# auth.js 的登录、改密和启动会话校验，以及 api.js 的 Token 刷新会进入本 Router。
# prefix 让本文件所有路径统一以 /api/auth 开头。Router 只处理 HTTP/Cookie 契约，
# 密码校验、Token 和 Refresh Session 的真实业务交给 services/auth.py。
router = APIRouter(prefix="/api/auth", tags=["auth"], responses=API_ERROR_RESPONSES)
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _authenticate(
    form: OAuth2PasswordRequestForm,
    session: AsyncSession,
) -> tuple[TokenResponse, str]:
    """校验登录表单，并创建 Access Token 与 Refresh Session。"""

    user = await auth_service.authenticate_user(session, form.username, form.password)
    if user is None:
        # 不区分“用户不存在”和“密码错误”，避免泄露账号存在性。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    refresh_token = await auth_service.create_refresh_session(session, user.id)
    token = TokenResponse(access_token=auth_service.create_access_token(user.id))
    return token, refresh_token


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """按统一配置写入 Refresh Token Cookie。"""

    settings = get_settings()
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_minutes * 60,
        path="/api",
    )


@router.post(
    "/token",
    response_model=ApiSuccess[TokenResponse],
    status_code=status.HTTP_200_OK,
)
async def login(
    request: Request,
    response: Response,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSession,
) -> ApiSuccess[TokenResponse]:
    """为博客前端登录并使用统一响应格式返回 Access Token。"""

    token, refresh_token = await _authenticate(form, session)
    _set_refresh_cookie(response, refresh_token)
    return success_response(request, token)


@router.post(
    "/oauth2-token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def oauth2_login(
    response: Response,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: DbSession,
) -> TokenResponse:
    """为 Swagger OAuth2 流程返回标准顶层 Token，不属于业务响应信封。"""

    token, refresh_token = await _authenticate(form, session)
    _set_refresh_cookie(response, refresh_token)
    return token


@router.post("/refresh", response_model=ApiSuccess[TokenResponse])
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> ApiSuccess[TokenResponse]:
    """使用 HttpOnly Cookie 换取新 Access Token，并轮换 Refresh Token。"""

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required")
    rotated = await auth_service.rotate_refresh_session(session, refresh_token)
    if rotated is None:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    user_id, new_refresh = rotated
    result = TokenResponse(access_token=auth_service.create_access_token(user_id))
    _set_refresh_cookie(response, new_refresh)
    return success_response(request, result)


@router.post("/logout", response_model=ApiSuccess[None], status_code=status.HTTP_200_OK)
async def logout(request: Request, response: Response) -> ApiSuccess[None]:
    """清除 Refresh Cookie，并返回可携带元数据的 200 成功响应。"""

    response.delete_cookie("refresh_token", path="/api")
    return success_response(request, None)


@router.get("/me", response_model=ApiSuccess[UserResponse])
async def current_session(request: Request, current_user: CurrentUser) -> ApiSuccess[UserResponse]:
    """验证当前 Access Token，并返回导航会话所需的当前用户。"""

    return success_response(request, UserResponse.model_validate(current_user))


@router.post("/password", response_model=ApiSuccess[None])
async def change_password(
    request: Request,
    data: PasswordChangeRequest,
    session: DbSession,
    current_user: CurrentUser,
) -> ApiSuccess[None]:
    """验证当前密码后修改密码；旧密码错误返回 400。"""

    if not await user_service.change_password(
        session, current_user, data.current_password, data.new_password
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    return success_response(request, None)
