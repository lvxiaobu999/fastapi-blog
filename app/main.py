from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core import get_settings
from app.db.session import engine
from app.routers import api_auth_router, api_posts_router, api_users_router, pages_router
from app.templating import APP_DIR, templates

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


@app.exception_handler(404)
async def not_found(request: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "The requested page was not found."
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": message})

    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": "Page not found", "status_code": 404, "message": message},
        status_code=status.HTTP_404_NOT_FOUND,
    )
