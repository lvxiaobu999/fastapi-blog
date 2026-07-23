"""博客 HTML 页面路由。

本模块只负责页面数据准备和模板渲染，不处理表单提交、认证或写入数据库。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.post import PostQueryParams
from app.services import posts as post_service
from app.services import users as user_service
from app.templating import templates

router = APIRouter(include_in_schema=False)
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/", name="home")
@router.get("/posts", name="posts")
def home(request: Request, session: DbSession, params: Annotated[PostQueryParams, Query()]):
    """渲染首页和帖子列表页，支持关键词与分页查询。"""

    posts = post_service.list_posts(session, params)
    return templates.TemplateResponse(
        request,
        "home.html",
        {"posts": posts, "title": "Home", "query": params},
    )


@router.get("/login", name="login")
def login_page(request: Request):
    """渲染登录表单；登录提交接口尚未实现。"""

    return templates.TemplateResponse(request, "login.html", {"title": "Login"})


@router.get("/register", name="register")
def register_page(request: Request):
    """渲染注册表单；注册提交接口尚未实现。"""

    return templates.TemplateResponse(request, "register.html", {"title": "Register"})


@router.get("/profile/{user_id}", name="profile")
def profile_page(request: Request, user_id: int, session: DbSession):
    """根据用户 ID 渲染资料页；当前未加入当前用户认证判断。"""

    user = user_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return templates.TemplateResponse(
        request,
        "profile.html",
        {"user": user, "title": "Profile"},
    )


@router.get("/posts/new", name="post_create")
def new_post_page(request: Request):
    """渲染新建帖子表单；表单提交接口尚未实现。"""

    return templates.TemplateResponse(request, "post_form.html", {"title": "New post"})


@router.get("/posts/{post_id}/edit", name="post_edit")
def edit_post_page(request: Request, post_id: int, session: DbSession):
    """查询并渲染指定帖子的编辑表单。"""

    post = post_service.get_post(session, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return templates.TemplateResponse(
        request,
        "post_form.html",
        {"post": post, "title": "Edit post"},
    )


@router.get("/posts/{post_id}", name="post")
def post_detail(request: Request, post_id: int, session: DbSession):
    """查询并渲染帖子详情。"""

    post = post_service.get_post(session, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return templates.TemplateResponse(
        request,
        "post.html",
        {"post": post, "title": post.title},
    )
