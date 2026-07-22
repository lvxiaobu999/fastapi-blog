"""用户数据访问与业务规则。"""

from pwdlib import PasswordHash
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.schemas import UserCreate, UserUpdate

# recommended() 当前会选择 Argon2。PasswordHash 可以安全复用，无需每次请求重复创建。
password_hash = PasswordHash.recommended()


class UserAlreadyExistsError(Exception):
    """用户名或邮箱违反唯一约束。"""


def _normalize_email(email: str) -> str:
    """统一邮箱大小写和首尾空格，避免同一邮箱以不同形式重复注册。"""

    return email.strip().lower()


def _ensure_unique_identity(
    session: Session,
    *,
    username: str,
    email: str,
    exclude_user_id: int | None = None,
) -> None:
    """在写入前检查用户名和邮箱，并可排除当前正在更新的用户。"""

    statement = select(User.id).where(or_(User.username == username, User.email == email))
    if exclude_user_id is not None:
        statement = statement.where(User.id != exclude_user_id)
    if session.scalar(statement) is not None:
        raise UserAlreadyExistsError


def create_user(session: Session, data: UserCreate) -> User:
    """规范化输入、哈希密码并在一个事务中创建用户。"""

    username = data.username.strip()
    email = _normalize_email(str(data.email))
    _ensure_unique_identity(session, username=username, email=email)

    user = User(
        username=username,
        email=email,
        # 数据库永远只接收 Argon2 哈希，不保存请求中的明文密码。
        hashed_password=password_hash.hash(data.password),
    )
    session.add(user)

    try:
        # commit() 会先 flush INSERT；数据库生成 id 和模型默认 nickname/is_admin。
        session.commit()
    except IntegrityError as exc:
        # 预查询不能解决并发竞争，最终仍以数据库唯一约束为准。
        session.rollback()
        raise UserAlreadyExistsError from exc

    session.refresh(user)
    return user


def list_users(session: Session, *, offset: int = 0, limit: int = 100) -> list[User]:
    """按用户 ID 稳定排序，返回指定范围的用户。"""

    statement = select(User).order_by(User.id).offset(offset).limit(limit)
    return list(session.scalars(statement))


def get_user(session: Session, user_id: int) -> User | None:
    """按主键查询用户；不存在时返回 None，由 Router 转换为 404。"""

    return session.get(User, user_id)


def update_user(session: Session, user: User, data: UserUpdate) -> User:
    """只更新客户端实际传入的字段，并在密码变化时重新生成哈希。"""

    changes = data.model_dump(exclude_unset=True)
    username = changes.get("username", user.username)
    email_value = changes.get("email", user.email)

    # username/email 不允许被更新为 null；其余可更新字段在下方分别处理。
    normalized_username = username.strip() if username is not None else user.username
    normalized_email = _normalize_email(str(email_value)) if email_value is not None else user.email
    _ensure_unique_identity(
        session,
        username=normalized_username,
        email=normalized_email,
        exclude_user_id=user.id,
    )

    user.username = normalized_username
    user.email = normalized_email
    if changes.get("password") is not None:
        user.hashed_password = password_hash.hash(changes["password"])
    if changes.get("nickname") is not None:
        user.nickname = changes["nickname"].strip()
    if "image_file" in changes:
        user.image_file = changes["image_file"]

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise UserAlreadyExistsError from exc

    session.refresh(user)
    return user


def delete_user(session: Session, user: User) -> None:
    """删除指定用户并提交事务。"""

    session.delete(user)
    session.commit()
