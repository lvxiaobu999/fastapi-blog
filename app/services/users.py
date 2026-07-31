"""用户数据访问与业务规则。"""

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User
from app.schemas import UserCreate, UserUpdate
from app.schemas.user import AdminUserCreate, AdminUserUpdate
from app.services.auth import hash_password, verify_password


class UserAlreadyExistsError(Exception):
    """用户名或邮箱违反唯一约束。"""


def _normalize_username(username: str) -> str:
    """去除用户名首尾空格并转为小写，保证存储和登录规则一致。"""

    return username.strip().lower()


def _normalize_email(email: str) -> str:
    """统一邮箱大小写和首尾空格，避免同一邮箱以不同形式重复注册。"""

    return email.strip().lower()


async def _ensure_unique_identity(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    exclude_user_id: int | None = None,
) -> None:
    """在写入前检查用户名和邮箱，并可排除当前正在更新的用户。"""

    # func.lower() 让数据库中已有的历史混合大小写数据也参与冲突检查；仅规范化新输入
    # 不能阻止旧值 "Alice" 与新值 "alice" 在区分大小写的数据库中并存。
    statement = select(User.id).where(
        or_(
            func.lower(User.username) == func.lower(username),
            func.lower(User.email) == func.lower(email),
        )
    )
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    if await session.scalar(statement) is not None:
        raise UserAlreadyExistsError


async def create_user(session: AsyncSession, data: UserCreate, *, is_admin: bool = False) -> User:
    """规范化输入、哈希密码并创建用户；管理员标记只能由后端可信调用方传入。"""

    username = _normalize_username(data.username)
    email = _normalize_email(str(data.email))
    await _ensure_unique_identity(session, username=username, email=email)

    # Argon2 是 CPU 密集操作，放入工作线程，避免阻塞处理其他请求的事件循环。
    hashed_password = await hash_password(data.password)

    user = User(
        username=username,
        email=email,
        # 数据库永远只接收 Argon2 哈希，不保存请求中的明文密码。
        hashed_password=hashed_password,
        # 公开注册 Router 不传该参数，普通访客不能通过请求体提升自身权限。
        is_admin=is_admin,
    )
    session.add(user)

    try:
        # commit() 会先 flush INSERT；数据库生成 id 和模型默认 nickname/is_admin。
        await session.commit()
    except IntegrityError as exc:
        # 预查询不能解决并发竞争，最终仍以数据库唯一约束为准。
        await session.rollback()
        raise UserAlreadyExistsError from exc

    await session.refresh(user)
    return user


async def list_users(
    session: AsyncSession,
    *,
    offset: int = 0,
    limit: int = 100,
) -> list[User]:
    """按用户 ID 稳定排序，返回指定范围的用户。"""

    statement = select(User).order_by(User.id).offset(offset).limit(limit)
    result = await session.scalars(statement)
    return list(result)


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    """按主键查询用户；不存在时返回 None，由 Router 转换为 404。"""

    return await session.get(User, user_id)


async def update_user(session: AsyncSession, user: User, data: UserUpdate) -> User:
    """只更新客户端实际传入的字段，并在密码变化时重新生成哈希。"""

    changes = data.model_dump(exclude_unset=True)
    username = changes.get("username", user.username)
    email_value = changes.get("email", user.email)

    # username/email 不允许被更新为 null；其余可更新字段在下方分别处理。
    normalized_username = _normalize_username(username) if username is not None else user.username
    normalized_email = _normalize_email(str(email_value)) if email_value is not None else user.email
    await _ensure_unique_identity(
        session,
        username=normalized_username,
        email=normalized_email,
        exclude_user_id=user.id,
    )

    user.username = normalized_username
    user.email = normalized_email
    if changes.get("password") is not None:
        user.hashed_password = await hash_password(changes["password"])
    if changes.get("nickname") is not None:
        user.nickname = changes["nickname"].strip()
    if "image_file" in changes:
        user.image_file = changes["image_file"]

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise UserAlreadyExistsError from exc

    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, user: User) -> None:
    """删除指定用户并提交事务。"""

    await session.delete(user)
    await session.commit()


async def create_admin_managed_user(session: AsyncSession, data: AdminUserCreate) -> User:
    """由管理员创建普通或管理员用户，并可设置初始昵称。"""

    user = await create_user(session, UserCreate.model_validate(data.model_dump()), is_admin=data.is_admin)
    if data.nickname:
        user.nickname = data.nickname.strip()
        await session.commit()
        await session.refresh(user)
    return user


async def update_admin_managed_user(
    session: AsyncSession, user: User, data: AdminUserUpdate
) -> User:
    """复用资料更新规则，并单独处理只有管理员能够改变的角色。"""

    profile_data = UserUpdate.model_validate(
        data.model_dump(exclude={"is_admin"}, exclude_unset=True)
    )
    user = await update_user(session, user, profile_data)
    if data.is_admin is not None and user.is_admin != data.is_admin:
        user.is_admin = data.is_admin
        await session.commit()
        await session.refresh(user)
    return user


async def set_profile_image(session: AsyncSession, user: User, filename: str) -> User:
    """保存头像文件名；文件内容已经由图片服务校验并写入磁盘。"""

    user.image_file = filename
    await session.commit()
    await session.refresh(user)
    return user


async def change_password(
    session: AsyncSession, user: User, current_password: str, new_password: str
) -> bool:
    """验证旧密码后写入新哈希；旧密码错误时不修改数据库并返回 False。"""

    if not await verify_password(current_password, user.hashed_password):
        return False
    user.hashed_password = await hash_password(new_password)
    await session.commit()
    return True
