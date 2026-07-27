from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.core import get_settings
from app.db.session import engine
from app.exception_handlers import register_exception_handlers
from app.routers import api_auth_router, api_posts_router, api_users_router, pages_router
from app.templating import APP_DIR

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用退出时释放异步数据库连接池。"""

    yield
    await engine.dispose()


app = FastAPI(title=settings.project_title, lifespan=lifespan)

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=APP_DIR / "media"), name="media")
app.include_router(pages_router)
app.include_router(api_auth_router)
app.include_router(api_posts_router)
app.include_router(api_users_router)
register_exception_handlers(app)
