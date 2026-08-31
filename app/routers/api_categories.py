"""管理员分类管理接口。

本模块只处理分类管理所需的 HTTP 参数、管理员权限和状态码；名称/slug 规范化、唯一性和
删除关联检查由 ``app.services.categories`` 负责。公开页面仍通过页面 Router 读取分类，
不会因为这里的管理接口暴露写权限。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import API_ERROR_RESPONSES, success_response
from app.db.session import get_db
from app.dependencies.auth import AdminUser
from app.schemas.api import ApiSuccess
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate
from app.services import categories as category_service

# Router 先确认 AdminUser，再调用 Service；页面隐藏入口不能替代这里的服务端权限校验。
router = APIRouter(
    prefix="/api/admin/categories",
    tags=["admin-categories"],
    responses=API_ERROR_RESPONSES,
)
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _get_category_or_404(session: AsyncSession, category_id: int):
    """查询后台操作目标，不存在时返回统一 404。"""

    category = await category_service.get_category_by_id(session, category_id)
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found")
    return category


def _response(category) -> CategoryResponse:
    """把 ORM 分类转换为只包含公开字段的响应 Schema。"""

    return CategoryResponse.model_validate(category)


@router.get("", response_model=ApiSuccess[list[CategoryResponse]])
async def list_admin_categories(
    request: Request, session: DbSession, _admin: AdminUser
) -> ApiSuccess[list[CategoryResponse]]:
    """管理员按展示顺序读取全部分类。"""

    categories = await category_service.list_categories(session)
    return success_response(request, [_response(category) for category in categories])


@router.post(
    "",
    response_model=ApiSuccess[CategoryResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_admin_category(
    request: Request,
    data: CategoryCreate,
    session: DbSession,
    _admin: AdminUser,
) -> ApiSuccess[CategoryResponse]:
    """由管理员创建分类；重复名称或 slug 返回 409。"""

    try:
        category = await category_service.create_category(session, data)
    except category_service.CategoryAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category name or slug already exists",
        ) from exc
    return success_response(request, _response(category))


@router.patch("/{category_id}", response_model=ApiSuccess[CategoryResponse])
async def update_admin_category(
    request: Request,
    category_id: int,
    data: CategoryUpdate,
    session: DbSession,
    _admin: AdminUser,
) -> ApiSuccess[CategoryResponse]:
    """由管理员部分更新分类；未提交的字段保持原值。"""

    category = await _get_category_or_404(session, category_id)
    try:
        category = await category_service.update_category(session, category, data)
    except category_service.CategoryAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category name or slug already exists",
        ) from exc
    return success_response(request, _response(category))


@router.delete("/{category_id}", response_model=ApiSuccess[None])
async def delete_admin_category(
    request: Request,
    category_id: int,
    session: DbSession,
    _admin: AdminUser,
) -> ApiSuccess[None]:
    """删除分类；若仍被文章使用则返回 409 并保留原文章关系。"""

    category = await _get_category_or_404(session, category_id)
    try:
        await category_service.delete_category(session, category)
    except category_service.CategoryInUseError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category is used by posts and cannot be deleted",
        ) from exc
    return success_response(request, None)
