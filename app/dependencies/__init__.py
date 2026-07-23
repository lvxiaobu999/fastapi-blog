"""FastAPI 共享依赖。"""

from .auth import CurrentUser, get_current_user, require_admin

__all__ = ["CurrentUser", "get_current_user", "require_admin"]
