from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
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


@app.middleware("http")
async def add_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """为每个请求生成追踪 ID，并在响应正文元数据与 Header 之间保持一致。"""

    # 服务端生成 ID，避免客户端伪造标识导致日志中的不同请求互相混淆。
    request.state.request_id = uuid4()
    response = await call_next(request)
    response.headers["X-Request-ID"] = str(request.state.request_id)
    return response

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=APP_DIR / "media"), name="media")
app.include_router(pages_router)
app.include_router(api_auth_router)
app.include_router(api_posts_router)
app.include_router(api_users_router)
register_exception_handlers(app)
