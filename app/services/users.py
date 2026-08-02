"""用户数据访问与业务规则。"""

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import User
from app.schemas import UserCreate, UserUpdate
from app.schemas.user import AdminUserCreate, AdminUserUpdate
from app.services.auth import hash_password, verify_password

# ==================== Service 入口导读 ====================
# 上游调用者：api_users、api_admin、pages 以及 auth Router。
# 本模块负责用户名/邮箱规范化、唯一性检查、密码哈希后的用户 CRUD、头像文件名和密码更新。
# Router 负责“谁有权限改”，这里负责“数据怎样合法地改”；写函数集中 commit，冲突时 rollback。
# 返回值通常是 User ORM 对象，Router 再通过 UserResponse Schema 过滤敏感字段。


class UserAlreadyExistsError(Exception):
    """用户名或邮箱违反唯一约束。"""


def _normalize_username(username: str) -> str:
    """把不同写法的用户名归一化，避免注册与登录把它们当成不同账号。

    例如用户误输入 `` Alice ``，系统应仍按 ``alice`` 保存和识别。这个内部步骤服务于
    注册、后台新增用户和唯一性检查，不代表允许普通用户修改 username。
    """

    return username.strip().lower()


def _normalize_email(email: str) -> str:
    """把邮箱统一为可比较形式，防止同一邮箱因大小写或空格重复注册。

    它服务于“一个邮箱只能对应一个账号”的产品规则，也让用户以后用邮箱登录时不必
    记住注册时使用了什么大小写。这里只转换字符串，不验证格式，格式由 Schema 负责。
    """

    return email.strip().lower()


async def _ensure_unique_identity(
    session: AsyncSession,
    *,
    username: str,
    email: str,
    exclude_user_id: int | None = None,
) -> None:
    """落实“用户名和邮箱不能被其他账号占用”的用户需求。

    注册或后台编辑用户时，页面需要尽早得到清晰的冲突提示，而不是直接暴露数据库错误。
    创建时检查所有用户；更新时通过 ``exclude_user_id`` 排除本人，否则用户不修改原邮箱
    也会被误判为与自己重复。本检查改善提示，数据库唯一约束负责兜住并发写入。
    """

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
    """完成公开注册和后台新增用户共同需要的“创建账号”主流程。

    用户需求：访客注册后获得普通账号；管理员也能在后台创建普通或管理员账号。两条入口
    的数据落库规则完全相同，因此集中在这里执行规范化、唯一性检查、密码哈希和事务提交。
    ``is_admin`` 不是公开请求字段，只允许管理员 Service 这个可信调用方传入，防止提权。
    成功返回带数据库主键/默认值的 User，冲突则回滚并抛出可由 Router 转为 409 的异常。
    """

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
    """为用户列表和后台用户管理表格提供可分页的数据。

    ``offset/limit`` 让页面只取当前需要的一段，避免用户增长后一次加载整张表。按 ID 排序
    是为了翻页时顺序可预测。权限与响应字段由各自 Router/Schema 控制，本函数只负责查询。
    """

    statement = select(User).order_by(User.id).offset(offset).limit(limit)
    result = await session.scalars(statement)
    return list(result)


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    """为个人主页、后台编辑以及认证后的用户定位取得指定账号。

    多个入口都需要先确认目标用户真实存在，所以复用主键查询。Service 返回 ``None`` 而不
    抛 HTTP 异常，是为了让资料页、管理接口等上游根据自身协议决定返回 404 或其他结果。
    """

    return await session.get(User, user_id)


async def update_user(session: AsyncSession, user: User, data: UserUpdate) -> User:
    """保存用户资料编辑结果，同时保证未提交的字段不会被意外清空。

    当前普通用户界面的需求是只能修改 nickname 和 email，username 作为登录标识保持不变；
    管理员入口也复用这套基础资料规则。``exclude_unset`` 区分“没有传字段”和“明确传值”，
    邮箱等身份字段重新做规范化和冲突检查，最后一次提交并刷新数据库生成的最终状态。
    """

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
    """执行用户主动注销或管理员后台删除账号的持久化步骤。

    Router 必须先完成“本人或管理员”的授权判断，并把确认存在的 User 传入；Service 不再
    猜测操作者身份，只删除对象并提交。相关内容是否级联删除由 Model/数据库外键规则决定。
    """

    await session.delete(user)
    await session.commit()


async def create_admin_managed_user(session: AsyncSession, data: AdminUserCreate) -> User:
    """满足后台“新增用户”表单比公开注册多出的管理需求。

    管理员可以指定初始昵称和角色，但密码安全、唯一性等规则不应另写一套，所以先调用
    ``create_user`` 完成通用创建，再保存只有后台表单才提供的昵称。调用前的管理员权限
    由 api_admin Router 保证。
    """

    user = await create_user(session, UserCreate.model_validate(data.model_dump()), is_admin=data.is_admin)
    if data.nickname:
        user.nickname = data.nickname.strip()
        await session.commit()
        await session.refresh(user)
    return user


async def update_admin_managed_user(
    session: AsyncSession, user: User, data: AdminUserUpdate
) -> User:
    """满足后台编辑用户资料及调整管理员角色的需求。

    普通资料交给 ``update_user`` 复用校验规则，``is_admin`` 则单独处理，因为角色调整只应
    出现在管理入口。Router 还负责禁止管理员撤销自己的权限，Service 负责最终数据写入。
    """

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
    """让用户在资料弹窗点击头像后立即把新头像绑定到自己的账号。

    图片 Service 已负责校验并保存文件，这里只更新 ``users.image_file``。把两项职责分开，
    是因为文件写磁盘与用户表更新属于不同操作；成功后 refresh，让 Router 立即返回新头像。
    """

    user.image_file = filename
    await session.commit()
    await session.refresh(user)
    return user


async def change_password(
    session: AsyncSession, user: User, current_password: str, new_password: str
) -> bool:
    """实现头像菜单“修改密码”弹窗的安全保存流程。

    即使用户当前已登录，也必须再次验证旧密码，避免他人拿到未锁定浏览器后直接接管账号。
    旧密码错误返回 False 且不写库，Router 据此给出业务提示；正确时只保存新密码的哈希并
    提交。确认新密码是否一致由请求 Schema 负责，避免业务层接收两个含义重复的值。
    """

    if not await verify_password(current_password, user.hashed_password):
        return False
    user.hashed_password = await hash_password(new_password)
    await session.commit()
    return True
