from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.services.posts import get_post, posts
from app.templating import templates

router = APIRouter(include_in_schema=False)


@router.get("/", name="home", response_class=HTMLResponse)
@router.get("/posts", name="posts", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"posts": posts, "title": "home"})


@router.get("/posts/{post_id}", name="post", response_class=HTMLResponse)
def post_detail(request: Request, post_id: int):
    post = get_post(post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return templates.TemplateResponse(
        request,
        "post.html",
        {"post": post, "title": "post detail"},
    )
