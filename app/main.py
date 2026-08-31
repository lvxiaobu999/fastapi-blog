"""组装 FastAPI 应用、全局中间件、健康检查、静态目录和业务 Router。

本模块是应用启动入口，不编写具体业务。一次 HTTP 请求会先经过 Host 校验和请求日志中间件，
再进入 Router、Depends、Service 与数据库；应用退出时由 lifespan 关闭 Redis 和数据库连接池。
"""

import logging
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from app.core import get_settings
from app.core.logging import bind_request_id, configure_logging, reset_request_id
from app.db.redis import close_redis, get_redis
from app.db.session import engine, get_db
from app.exception_handlers import register_exception_handlers
from app.routers import (
    api_activities_router,
    api_admin_router,
    api_auth_router,
    api_categories_router,
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
    await close_redis()
    await engine.dispose()


app = FastAPI(title=settings.project_title, lifespan=lifespan)
# Host 是 HTTP 请求头中的站点名称。中间件在业务 Router 之前拒绝不在白名单中的 Host，
# 防止攻击者伪造域名影响跳转或绝对 URL。开发和生产白名单由 Settings 统一提供。
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

# CSP 告诉浏览器“脚本、图片、WebSocket 可以从哪里加载”。脚本只允许本站；Toast UI 会
# 生成少量行内 style 属性，所以 style-src 暂时保留 unsafe-inline，但 script-src 不放开它。
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


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
        # 应用层也设置安全头，确保绕过 Nginx 的本地调试或内部访问不会失去基础防护。
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
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


DbSession = Annotated[AsyncSession, Depends(get_db)]
RedisClient = Annotated[Redis, Depends(get_redis)]


@app.get("/health/live", include_in_schema=False)
async def health_live() -> dict[str, str]:
    """确认 Web 进程仍能处理请求，不访问数据库或 Redis。

    liveness 失败时编排系统可以重启进程；数据库短暂故障不应触发这里失败，否则应用可能在
    外部依赖故障期间不断重启。
    """

    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
async def health_ready(session: DbSession, redis: RedisClient) -> dict[str, str]:
    """确认数据库与 Redis 均可用，供部署编排决定是否接收流量。

    readiness 通过依赖注入取得与普通请求相同的 AsyncSession 和 Redis 客户端，再执行最小
    只读命令。任一依赖失败都返回 503，表示进程还活着，但暂时不应接收业务流量。
    """

    try:
        await session.execute(text("SELECT 1"))
        await redis.ping()
    except (SQLAlchemyError, RedisError, OSError, TimeoutError) as exc:
        # 对外只返回依赖不可用，不暴露主机、端口、凭据或底层驱动错误。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service dependencies are unavailable",
        ) from exc
    return {"status": "ready"}


# 静态资源和媒体文件先挂载，业务 Router 再按功能注册。include_router 只负责把各模块端点
# 加入应用；端点内部仍按 Depends -> Service -> ORM/Redis 的方向执行。
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/media", StaticFiles(directory=APP_DIR / "media"), name="media")
app.include_router(pages_router)
app.include_router(api_activities_router)
app.include_router(api_admin_router)
app.include_router(api_auth_router)
app.include_router(api_categories_router)
app.include_router(api_posts_router)
app.include_router(comments_router)
app.include_router(api_users_router)
register_exception_handlers(app)
