"""Bearer Token 提取和当前用户权限依赖；认证失败统一转换为 HTTP 401。"""

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import User
from app.services.auth import verify_access_token

# FastAPI 从 Authorization: Bearer <token> Header 中提取字符串，并在缺失时自动返回
# 401。tokenUrl 还会让 Swagger 的 Authorize 按钮知道登录接口在哪里。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
DbSession = Annotated[AsyncSession, Depends(get_db)]


def _credentials_error() -> HTTPException:
    """构造一致的 Bearer 认证失败响应。

    401 表示“身份没有被确认”；``WWW-Authenticate`` 告诉符合 OAuth2 约定的客户端
    应该使用 Bearer 方案重试。用户名不存在、Token 过期和签名错误都使用同一响应，
    避免通过错误差异枚举账号或 Token 状态。
    """

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], session: DbSession
) -> User:
    """验证 JWT 并从数据库加载当前用户，确保授权依据是最新状态。

    Token 只证明“当时签发给哪个用户”，不会实时携带管理员状态。因此每次受保护
    请求仍要查库，才能在用户被禁用或管理员标记变化后立即使用新状态。
    """

    try:
        user_id = verify_access_token(token)
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise _credentials_error() from exc
    user = await session.get(User, user_id)
    if user is None:
        raise _credentials_error()
    return user


async def require_admin(user: Annotated[User, Depends(get_current_user)]) -> User:
    """只允许已认证管理员继续请求。

    这里依赖 ``get_current_user``，所以执行顺序是先认证再授权：没有身份返回 401，
    身份有效但 ``is_admin`` 为 False 返回 403。管理员标记来自数据库，不接受前端提交。
    """

    # for key, value in vars(user).items():
    #     if key.startswith("_"):  # 去掉 SQLAlchemy 内部属性
    #         continue
    #     print(f"哈哈----{key} --> {value}")

    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]
