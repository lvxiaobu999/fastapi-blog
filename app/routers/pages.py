"""博客 HTML 页面路由。

本模块只负责页面数据准备和模板渲染，不处理表单提交、认证或写入数据库。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.post import PostQueryParams
from app.services import categories as category_service
from app.services import posts as post_service
from app.services import users as user_service
from app.templating import templates

# ==================== Router 入口导读 ====================
# 这里处理浏览器“直接打开页面”的 GET 请求，例如地址栏访问 /posts/1，或模板里的
# url_for(...) 链接跳转。它负责查询模板首屏需要的数据并返回 HTML，不处理 AJAX 写操作。
# 页面中的后续登录、发帖、点赞、评论等动作会由 static/js 再调用对应 /api Router。
# include_in_schema=False 表示这些 HTML 页面不出现在 Swagger API 文档中。
router = APIRouter(include_in_schema=False)
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/", name="home")
@router.get("/posts", name="posts")
async def home(
    request: Request,
    session: DbSession,
    params: Annotated[PostQueryParams, Query()],
):
    """渲染首页和帖子列表页，支持分类、关键词与分页查询。"""

    posts = await post_service.list_posts(session, params)
    categories = await category_service.list_categories(session)
    selected_category = next(
        (category for category in categories if category.slug == params.category), None
    )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "posts": posts,
            "categories": categories,
            "selected_category": selected_category,
            "title": "Home",
            "query": params,
        },
    )


@router.get("/login", name="login")
async def login_page(request: Request):
    """渲染登录表单；登录提交接口尚未实现。"""

    return templates.TemplateResponse(request, "login.html", {"title": "Login"})


@router.get("/register", name="register")
async def register_page(request: Request):
    """渲染注册表单；注册提交接口尚未实现。"""

    return templates.TemplateResponse(request, "register.html", {"title": "Register"})


@router.get("/profile/{user_id}", name="profile")
async def profile_page(request: Request, user_id: int, session: DbSession):
    """根据用户 ID 渲染资料页；当前未加入当前用户认证判断。"""

    user = await user_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return templates.TemplateResponse(
        request,
        "profile.html",
        {"user": user, "title": "Profile"},
    )


@router.get("/me/activities", name="my_activities")
async def my_activities_page(request: Request):
    """渲染当前用户活动列表外壳；真实数据由受保护 API 按标签加载。"""

    return templates.TemplateResponse(request, "activities.html", {"title": "我的活动"})


@router.get("/posts/new", name="post_create")
async def new_post_page(request: Request, session: DbSession):
    """渲染带分类选择的新建帖子表单。"""

    categories = await category_service.list_categories(session)
    return templates.TemplateResponse(
        request,
        "post_form.html",
        {"categories": categories, "title": "New post"},
    )


@router.get("/posts/{post_id}/edit", name="post_edit")
async def edit_post_page(request: Request, post_id: int, session: DbSession):
    """查询并渲染指定帖子的编辑表单。"""

    post = await post_service.get_post(session, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    categories = await category_service.list_categories(session)

    return templates.TemplateResponse(
        request,
        "post_form.html",
        {"post": post, "categories": categories, "title": "Edit post"},
    )


@router.get("/posts/{post_id}", name="post")
async def post_detail(request: Request, post_id: int, session: DbSession):
    """查询并渲染帖子详情。"""

    post = await post_service.get_post(session, post_id, include_unpublished=False)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    return templates.TemplateResponse(
        request,
        "post.html",
        {"post": post, "title": post.title},
    )


@router.get("/admin", name="admin_dashboard")
async def admin_dashboard(request: Request):
    """渲染后台首页外壳；管理员身份由前端启动校验和 API 双重确认。"""

    return templates.TemplateResponse(request, "admin/dashboard.html", {"title": "后台概览"})


@router.get("/admin/users", name="admin_users")
async def admin_users(request: Request):
    """渲染用户管理工作区，数据由管理员 API 加载。"""

    return templates.TemplateResponse(request, "admin/users.html", {"title": "用户管理"})


@router.get("/admin/posts", name="admin_posts")
async def admin_posts(request: Request):
    """渲染帖子管理工作区，数据由帖子 API 加载。"""

    return templates.TemplateResponse(request, "admin/posts.html", {"title": "帖子管理"})
