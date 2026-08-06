from contextlib import asynccontextmanager
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from fastapi.staticfiles import StaticFiles

from app.core import get_settings
from app.core.logging import bind_request_id, configure_logging, reset_request_id
from app.db.session import engine
from app.exception_handlers import register_exception_handlers
from app.routers import (
    api_activities_router,
    api_admin_router,
    api_auth_router,
    api_posts_router,
    api_users_router,
    comments_router,
    pages_router,
)
from app.templating import APP_DIR

settings = get_settings()
configure_logging(settings)
# 参数不是输出文本，而是 Logger 的稳定名称。"app.access" 表示 app 命名空间下专门记录
# HTTP 访问事件的子 Logger；JSON 中 logger 字段会显示该名称，线上可以据此单独筛选、
# 调整等级或路由到其他 Handler。它默认把事件向上传播给 configure_logging 配置的 root。
access_logger = logging.getLogger("app.access")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """应用退出时释放异步数据库连接池。"""

    yield
    await engine.dispose()


app = FastAPI(title=settings.project_title, lifespan=lifespan)


@app.middleware("http")
async def add_request_id(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """生成请求 ID，并记录可按请求关联查询的访问日志与处理耗时。"""

    # 服务端生成 ID，避免客户端伪造标识导致日志中的不同请求互相混淆。
    request.state.request_id = uuid4()
    context_token = bind_request_id(request.state.request_id)
    started_at = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response
    finally:
        # 不记录查询参数和请求体，避免搜索词、Token、密码等敏感内容进入长期日志。
        access_logger.info(
            "HTTP request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "duration_ms": round((perf_counter() - started_at) * 1000, 2),
            },
        )
        reset_request_id(context_token)


app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=APP_DIR / "media"), name="media")
app.include_router(pages_router)
app.include_router(api_activities_router)
app.include_router(api_admin_router)
app.include_router(api_auth_router)
app.include_router(api_posts_router)
app.include_router(comments_router)
app.include_router(api_users_router)
register_exception_handlers(app)
