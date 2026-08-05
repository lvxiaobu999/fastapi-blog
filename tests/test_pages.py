"""异步 HTML 页面 Router 测试。"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User
from app.schemas.post import PostCreate
from app.services.posts import create_post

pytestmark = pytest.mark.anyio


@pytest.fixture
async def seeded_ids(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_categories: dict[str, int],
) -> tuple[int, int]:
    async with session_factory() as session:
        user = User(username="author", email="author@example.com", hashed_password="hash")
        session.add(user)
        await session.commit()
        post = await create_post(
            session,
            PostCreate(
                title="FastAPI page",
                content="Page body",
                user_id=user.id,
                category_id=seeded_categories["fastapi"],
            ),
        )
        return user.id, post.id


async def test_page_router_renders_pages(
    client: AsyncClient,
    seeded_ids: tuple[int, int],
) -> None:
    user_id, post_id = seeded_ids
    routes = [
        "/",
        "/posts",
        "/login",
        "/register",
        "/posts/new",
        f"/posts/{post_id}",
        f"/posts/{post_id}/edit",
        f"/profile/{user_id}",
        "/me/activities",
    ]

    responses = [await client.get(route) for route in routes]

    assert all(response.status_code == 200 for response in responses)
    assert "FastAPI page" in responses[1].text
    assert "author@example.com" in responses[7].text
    assert "data-avatar-editor" in responses[7].text
    assert "data-avatar-input" in responses[7].text
    assert "data-profile-avatar-image" in responses[7].text
    assert 'id="username"' in responses[7].text
    assert "readonly" in responses[7].text
    assert "data-activity-page" in responses[8].text


async def test_home_lists_categories_and_filters_posts(
    client: AsyncClient,
    seeded_ids: tuple[int, int],
) -> None:
    _, post_id = seeded_ids

    all_posts = await client.get("/posts")
    fastapi_posts = await client.get("/posts", params={"category": "fastapi"})
    python_posts = await client.get("/posts", params={"category": "python"})

    assert 'aria-label="文章分类"' in all_posts.text
    assert "FastAPI" in all_posts.text
    assert "Python" in all_posts.text
    assert f"/posts/{post_id}" in fastapi_posts.text
    assert "FastAPI文章" in fastapi_posts.text
    assert f"/posts/{post_id}" not in python_posts.text


async def test_page_router_returns_html_404(client: AsyncClient) -> None:
    response = await client.get("/posts/999")

    assert response.status_code == 404
    assert "页面不存在" in response.text
    assert "text/html" in response.headers["content-type"]


async def test_permission_denied_page_returns_html_403(client: AsyncClient) -> None:
    """普通用户权限校验失败后的落地页必须保留真实 403 状态。"""

    response = await client.get("/forbidden")

    assert response.status_code == 403
    assert "没有访问权限" in response.text
    assert "没有执行当前操作所需的权限" in response.text
    assert "text/html" in response.headers["content-type"]


async def test_layout_uses_auth_modals_and_es_modules(client: AsyncClient) -> None:
    """导航登录注册只打开模态框，前端写操作由 ES module 接管。"""

    response = await client.get("/")
    assert response.status_code == 200
    assert 'data-bs-target="#loginModal"' in response.text
    assert 'data-bs-target="#registerModal"' in response.text
    assert 'type="module"' in response.text
    assert "/static/js/auth.js" in response.text
    assert "dataset.authState" in response.text
    assert 'localStorage.getItem("blog-access-token")' in response.text


async def test_layout_uses_global_debounced_search_modal(client: AsyncClient) -> None:
    response = await client.get("/")
    search_script = await client.get("/static/js/search.js")

    assert response.status_code == 200
    assert 'data-bs-target="#searchModal"' in response.text
    assert "data-site-search" in response.text
    assert "/static/js/search.js" in response.text
    assert "data-search-form" not in response.text
    assert search_script.status_code == 200
    assert "SEARCH_DELAY_MS = 300" in search_script.text
    assert 'url: "/api/posts/search"' in search_script.text
    assert "pendingRequest?.abort()" in search_script.text


async def test_layout_includes_loading_and_session_scripts(client: AsyncClient) -> None:
    response = await client.get("/")
    ui_script = await client.get("/static/js/ui.js")

    assert response.status_code == 200
    assert "/static/js/auth.js" in response.text
    assert ui_script.status_code == 200
    assert "setButtonLoading" in ui_script.text
    assert "dataset.authState" in response.text


async def test_layout_has_authenticated_user_menu_and_password_modal(client: AsyncClient) -> None:
    """登录导航提供头像菜单、资料入口、管理员入口和改密模态框。"""

    response = await client.get("/")

    assert response.status_code == 200
    assert "data-current-user-avatar" in response.text
    assert "data-profile-link" in response.text
    assert "发布新帖子" in response.text
    assert "user-popover-publish" not in response.text
    assert response.text.index("data-theme-toggle") < response.text.index("user-avatar-trigger")
    assert "theme-icon-moon" in response.text
    assert "theme-icon-sun" in response.text
    assert "theme-option" not in response.text
    assert 'data-auth-admin' in response.text
    assert '/admin"' in response.text
    assert 'id="passwordModal"' in response.text
    assert "data-password-form" in response.text
    assert "?tab=comments" in response.text
    assert "?tab=likes" in response.text
    assert "?tab=favorites" in response.text
    assert "?tab=views" in response.text
    assert "user-footprint-link" not in response.text
    assert "user-popover-link" in response.text


async def test_admin_pages_use_separate_layout(client: AsyncClient) -> None:
    """后台页面共享独立侧栏布局，并加载后台专用脚本。"""

    dashboard, users, posts = await client.get("/admin"), await client.get("/admin/users"), await client.get("/admin/posts")

    assert dashboard.status_code == users.status_code == posts.status_code == 200
    assert "admin-shell" in dashboard.text
    assert "用户管理" in users.text
    assert "帖子管理" in posts.text
    assert "/static/js/admin.js" in dashboard.text
    assert "/static/js/auth.js" in dashboard.text
    assert 'id="loginModal"' in dashboard.text
    assert "data-login-form" in dashboard.text
    assert "admin-theme-toggle" in dashboard.text
    assert "/static/js/theme.js" in dashboard.text
    assert "theme-option" not in dashboard.text
    assert 'id="adminDeleteModal"' in dashboard.text
    assert "data-delete-confirm" in dashboard.text

    admin_script = await client.get("/static/js/admin.js")
    assert 'window.location.replace("/forbidden")' in admin_script.text
    assert "window.confirm" not in admin_script.text
    assert "deleteModal?.show()" in admin_script.text
    assert "登录状态已失效，请返回博客重新登录。" not in admin_script.text


async def test_post_pages_include_rich_editor_and_markdown_viewer(
    client: AsyncClient, seeded_ids: tuple[int, int]
) -> None:
    """编辑页加载 Toast UI，详情页通过 Viewer 渲染 Markdown。"""

    _, post_id = seeded_ids
    editor = await client.get("/posts/new")
    viewer = await client.get(f"/posts/{post_id}")

    assert "toastui-editor-all.min.js" in editor.text
    assert 'id="post-editor"' in editor.text
    assert 'name="category_id"' in editor.text
    assert "data-post-preview" in editor.text
    assert "editor-preview-button" in editor.text
    assert "btn-outline-primary" not in editor.text
    assert "admin-shell" in editor.text
    assert "admin-editor-shell" in editor.text
    assert "data-admin-content" in editor.text
    assert 'id="post-preview-viewer"' in editor.text
    assert "/static/js/admin.js" in editor.text
    assert "/static/js/posts.js" in editor.text
    assert "toastui-editor-all.min.js" in viewer.text
    assert 'id="post-viewer"' in viewer.text
    assert "/static/js/posts.js" in viewer.text
    assert "data-post-interactions" in viewer.text
    assert "data-view-count" in viewer.text
    assert "post-view-stat" in viewer.text
    assert "post-view-stat" in (await client.get("/posts")).text
    assert "/static/js/post-activities.js" in viewer.text
