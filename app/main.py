from fastapi import FastAPI, Request, status
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.routers import api_posts_router, pages_router
from app.templating import APP_DIR, templates

app = FastAPI()

app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(pages_router)
app.include_router(api_posts_router)


@app.exception_handler(404)
def not_found(request: Request, exc: StarletteHTTPException):
    message = exc.detail if isinstance(exc.detail, str) else "The requested page was not found."
    return templates.TemplateResponse(
        request,
        "error.html",
        {"title": "Page not found", "status_code": 404, "message": message},
        status_code=status.HTTP_404_NOT_FOUND,
    )
