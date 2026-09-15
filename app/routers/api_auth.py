"""OAuth2 Password Flow 登录接口；负责 HTTP 表单解析和认证失败响应。"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.core import get_settings
from app.db.redis import get_redis
from app.db.session import get_db
from app.dependencies.auth import CurrentUser
from app.models import User
from app.schemas.api import ApiSuccess
from app.schemas.auth import (
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordSetRequest,
    TokenResponse,
)
from app.schemas.user import UserResponse
from app.services import auth as auth_service
from app.services import email as email_service
from app.services import password_reset as password_reset_service
from app.services import qq_oauth
from app.services import refresh_sessions as refresh_session_service
from app.services import users as user_service

# ==================== Router 入口导读 ====================
# auth.js 的登录、改密和启动会话校验，以及 api.js 的 Token 刷新会进入本 Router。
# prefix 让本文件所有路径统一以 /api/auth 开头。Router 只处理 HTTP/Cookie 契约，
# 密码/JWT 交给 services/auth.py，Redis Refresh 生命周期交给 services/refresh_sessions.py。
router = APIRouter(prefix="/api/auth", tags=["auth"], responses=API_ERROR_RESPONSES)
DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


async def _authenticate(
    form: OAuth2PasswordRequestForm,
    session: AsyncSession,
    redis: Redis,
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
    refresh_token = await refresh_session_service.create_refresh_session(redis, user.id)
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
    redis: RedisClient,
) -> ApiSuccess[TokenResponse]:
    """为博客前端登录并使用统一响应格式返回 Access Token。"""

    token, refresh_token = await _authenticate(form, session, redis)
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
    redis: RedisClient,
) -> TokenResponse:
    """为 Swagger OAuth2 流程返回标准顶层 Token，不属于业务响应信封。"""

    token, refresh_token = await _authenticate(form, session, redis)
    _set_refresh_cookie(response, refresh_token)
    return token


def _qq_redirect(next_path: str = "/", result: str = "success") -> RedirectResponse:
    """把 QQ 回调结果带回登录页；Access Token 不放在 URL，稍后由 Refresh Cookie 换取。"""

    query = urlencode({"qq": result, "next": next_path})
    return RedirectResponse(url=f"/login?{query}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/qq/login", include_in_schema=False)
async def qq_login(
    redis: RedisClient,
    next_path: str | None = None,
) -> RedirectResponse:
    """创建 QQ OAuth state 并跳转到 QQ 授权页。"""

    if not qq_oauth.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="QQ login is not configured",
        )
    state, _ = await qq_oauth.create_state(redis, next_path)
    return RedirectResponse(
        url=qq_oauth.authorization_url(state),
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/qq/callback", include_in_schema=False)
async def qq_callback(
    session: DbSession,
    redis: RedisClient,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """处理 QQ 回调，创建本项目 Refresh Session 后回到登录页完成会话交换。"""

    if error or not code or not state:
        return _qq_redirect(result="error")
    try:
        next_path = await qq_oauth.consume_state(redis, state)
        access_token = await qq_oauth.exchange_code(code)
        profile = await qq_oauth.fetch_profile(access_token)
        user = await qq_oauth.get_or_create_user(session, profile)
        refresh_token = await refresh_session_service.create_refresh_session(redis, user.id)
    except qq_oauth.QQOAuthStateError:
        return _qq_redirect(result="error")
    except qq_oauth.QQOAuthError:
        return _qq_redirect(result="error")
    response = _qq_redirect(next_path)
    _set_refresh_cookie(response, refresh_token)
    return response


@router.post("/refresh", response_model=ApiSuccess[TokenResponse])
async def refresh(
    request: Request,
    response: Response,
    session: DbSession,
    redis: RedisClient,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> ApiSuccess[TokenResponse]:
    """使用 HttpOnly Cookie 换取新 Access Token，并轮换 Refresh Token。"""

    if not refresh_token:
        raise HTTPException(status_code=401, detail="Refresh token required")
    rotated = await refresh_session_service.rotate_refresh_session(redis, refresh_token)
    if rotated is None:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    user_id, new_refresh = rotated
    # Redis 会话只保存最小 user_id。签发新 JWT 前仍检查数据库用户存在，防止用户删除后
    # 遗留的短期 Redis Key 被用来恢复身份。
    user = await session.get(User, user_id)
    if user is None:
        await refresh_session_service.revoke_refresh_session(redis, new_refresh)
        raise HTTPException(status_code=401, detail="Refresh token expired")
    result = TokenResponse(access_token=auth_service.create_access_token(user_id))
    _set_refresh_cookie(response, new_refresh)
    return success_response(request, result)


@router.post("/logout", response_model=ApiSuccess[None], status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    response: Response,
    redis: RedisClient,
    refresh_token: Annotated[str | None, Cookie()] = None,
) -> ApiSuccess[None]:
    """撤销 Redis Refresh Session 并清除 Cookie；重复退出保持幂等。"""

    if refresh_token:
        await refresh_session_service.revoke_refresh_session(redis, refresh_token)
    response.delete_cookie("refresh_token", path="/api")
    return success_response(request, None)


@router.get("/me", response_model=ApiSuccess[UserResponse])
async def current_session(request: Request, current_user: CurrentUser) -> ApiSuccess[UserResponse]:
    """验证当前 Access Token，并返回导航会话所需的当前用户。"""

    return success_response(request, UserResponse.model_validate(current_user))


@router.post("/password-reset/request", response_model=ApiSuccess[None])
async def request_password_reset_code(
    request: Request,
    data: PasswordResetRequest,
    session: DbSession,
    redis: RedisClient,
) -> ApiSuccess[None]:
    """向邮箱发送一次性验证码；无论邮箱是否注册都返回相同成功消息。"""

    try:
        await password_reset_service.request_password_reset(session, redis, str(data.email))
    except email_service.EmailDeliveryError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service temporarily unavailable",
        ) from exc
    return success_response(
        request,
        None,
        message="If the email is registered, a verification code has been sent",
    )


@router.post("/password-reset/confirm", response_model=ApiSuccess[None])
async def confirm_password_reset(
    request: Request,
    data: PasswordResetConfirm,
    session: DbSession,
    redis: RedisClient,
) -> ApiSuccess[None]:
    """校验邮箱验证码后更新密码，并撤销该用户的所有 Refresh Session。"""

    try:
        await password_reset_service.confirm_password_reset(
            session,
            redis,
            email=str(data.email),
            code=data.code,
            new_password=data.new_password,
        )
    except password_reset_service.PasswordResetCodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification code",
        ) from exc
    return success_response(request, None, message="Password reset successful")


@router.post("/password", response_model=ApiSuccess[None])
async def change_password(
    request: Request,
    response: Response,
    data: PasswordChangeRequest,
    session: DbSession,
    redis: RedisClient,
    current_user: CurrentUser,
) -> ApiSuccess[None]:
    """验证当前密码后修改密码；旧密码错误返回 400。"""

    if not await user_service.stage_password_change(
        session, current_user, data.current_password, data.new_password
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect"
        )
    # 密码变化属于高风险账户事件，撤销该用户所有设备的 Refresh Session。现有无状态
    # Access JWT 最长仍可使用到自身 exp，因此生产应保持较短 Access Token 有效期。
    # 先撤销会话再提交密码。Redis 失败时依赖的异常处理器返回 503，而请求级 Session
    # 关闭时会丢弃尚未提交的新哈希，避免响应失败但密码已经改变的跨存储部分提交。
    await refresh_session_service.revoke_user_refresh_sessions(redis, current_user.id)
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    response.delete_cookie("refresh_token", path="/api")
    return success_response(request, None)


@router.post("/password/set", response_model=ApiSuccess[None])
async def set_password(
    request: Request,
    response: Response,
    data: PasswordSetRequest,
    session: DbSession,
    redis: RedisClient,
    current_user: CurrentUser,
) -> ApiSuccess[None]:
    """让没有本站密码的第三方登录用户在已认证会话中首次设置本地密码。

    第三方 OAuth 已经完成身份认证，因此首次设置不要求不存在的旧密码；但接口只接受绑定了
    ``provider/provider_user_id`` 且 ``hashed_password`` 为空的账号。设置完成后撤销所有 Refresh Session，
    前端清除当前 Access Token 并要求重新登录，避免旧设备继续保持长期会话。
    """

    # 只有已绑定第三方身份且尚未设置本站密码的账号，才能走首次设置流程。
    # 判断使用通用复合字段，未来微信登录无需再增加一套接口。
    if not (current_user.provider and current_user.provider_user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password setup requires an external login account",
        )
    if current_user.hashed_password is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Password is already set; use the change-password endpoint",
        )
    if not await user_service.stage_password_setup(session, current_user, data.new_password):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Password is already set; use the change-password endpoint",
        )
    # 与修改密码保持相同的会话撤销边界：Redis 失败时不会提交尚未 commit 的新哈希。
    await refresh_session_service.revoke_user_refresh_sessions(redis, current_user.id)
    try:
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    response.delete_cookie("refresh_token", path="/api")
    return success_response(request, None, message="Password set successfully")
