"""统一处理页面与 API 异常。

页面请求渲染易理解的 HTML 错误页；``/api/`` 请求返回稳定的 JSON 错误契约。
本模块只负责把异常转换成 HTTP 响应，不捕获或隐藏 Router、Service 内的业务错误。
"""

import logging
from collections.abc import Mapping
from typing import Any, cast

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from app.templating import templates

logger = logging.getLogger(__name__)

_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    500: "INTERNAL_SERVER_ERROR",
}

_PAGE_TEMPLATES = {
    status.HTTP_401_UNAUTHORIZED: "error_401.html",
    status.HTTP_403_FORBIDDEN: "error_403.html",
    status.HTTP_404_NOT_FOUND: "error_404.html",
}

_PAGE_TITLES = {
    status.HTTP_401_UNAUTHORIZED: "需要登录",
    status.HTTP_403_FORBIDDEN: "没有访问权限",
    status.HTTP_404_NOT_FOUND: "页面不存在",
}


def _is_api_request(request: Request) -> bool:
    """判断请求是否属于 API；API 与页面使用不同的错误表现形式。"""

    return request.url.path.startswith("/api/")


def _error_code(status_code: int) -> str:
    """把 HTTP 状态码转换为便于前端判断的稳定错误代码。"""

    return _ERROR_CODES.get(status_code, "HTTP_ERROR")


def api_error_response(
    *,
    status_code: int,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """创建统一 API 错误响应，不向客户端暴露 Python 异常或调用栈。"""

    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=jsonable_encoder(
            {
                "success": False,
                "error": {
                    "status": status_code,
                    "code": _error_code(status_code),
                    "message": message,
                    "details": details,
                },
            },
        ),
    )


async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> Response:
    """处理预期 HTTP 异常，例如认证失败、权限不足和资源不存在。"""

    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    if _is_api_request(request):
        return api_error_response(
            status_code=exc.status_code,
            message=message,
            details=None if isinstance(exc.detail, str) else exc.detail,
            headers=exc.headers,
        )

    template_name = _PAGE_TEMPLATES.get(exc.status_code, "error.html")
    return templates.TemplateResponse(
        request,
        template_name,
        {
            "title": _PAGE_TITLES.get(exc.status_code, "请求无法完成"),
            "status_code": exc.status_code,
            "message": message,
        },
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> Response:
    """处理 FastAPI 参数校验失败，并保留字段级错误供前端表单展示。"""

    if _is_api_request(request):
        return api_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="Request validation failed",
            details=exc.errors(),
        )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "title": "请求参数错误",
            "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "message": "页面地址中的参数格式不正确，请检查后重试。",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


async def unexpected_exception_handler(request: Request, exc: Exception) -> Response:
    """兜底处理未知异常；服务端记录原始错误，客户端只接收安全提示。"""

    # exc_info 保留完整调用栈，便于开发环境定位问题；响应中绝不能返回该内容。
    logger.error(
        "Unhandled exception while processing %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    if _is_api_request(request):
        return api_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
        )

    return templates.TemplateResponse(
        request,
        "error_500.html",
        {"title": "服务器异常"},
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 应用上注册异常类型与统一处理函数的映射。"""

    # Starlette 的 ExceptionHandler 同时包含 HTTP 和 WebSocket 两种函数签名，Pylance
    # 无法根据第一个异常类型参数自动缩窄联合类型。这里显式转换注册边界的类型，处理
    # 函数本身仍保留具体异常类型，内部访问 detail/errors 时继续受到静态检查保护。
    app.add_exception_handler(
        StarletteHTTPException,
        cast(ExceptionHandler, http_exception_handler),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_exception_handler),
    )
    app.add_exception_handler(
        Exception,
        cast(ExceptionHandler, unexpected_exception_handler),
    )
