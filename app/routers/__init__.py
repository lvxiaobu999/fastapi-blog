from .api_activities import router as api_activities_router
from .api_admin import router as api_admin_router
from .api_auth import router as api_auth_router
from .api_categories import router as api_categories_router
from .api_posts import router as api_posts_router
from .api_users import router as api_users_router
from .comments import router as comments_router
from .pages import router as pages_router

__all__ = [
    "api_activities_router",
    "api_admin_router",
    "api_auth_router",
    "api_categories_router",
    "api_posts_router",
    "api_users_router",
    "comments_router",
    "pages_router",
]
