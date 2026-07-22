from .api_posts import router as api_posts_router
from .api_users import router as api_users_router
from .pages import router as pages_router

__all__ = ["api_posts_router", "api_users_router", "pages_router"]
