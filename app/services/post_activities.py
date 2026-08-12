"""文章互动和个人活动查询服务。

所有写操作都使用 Router 注入的 AsyncSession，并在单个用户动作内集中提交。
"""

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Comment, Post, PostFavorite, PostLike, PostView
from app.schemas.post import (
    PostInteractionState,
    UserActivityItem,
    UserCommentActivityItem,
)


def _dialect_insert(session: AsyncSession, model):
    """返回支持 ``ON CONFLICT`` 的当前数据库 Insert 构造器。

    开发和测试使用 SQLite，生产使用 PostgreSQL；二者都有方言级 upsert API。Settings
    已限制项目只使用这两种数据库，其他方言直接失败比退回有竞态的先查后写更安全。
    """

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        return postgresql_insert(model)
    if dialect_name == "sqlite":
        return sqlite_insert(model)
    raise RuntimeError(f"Unsupported database dialect for atomic interaction: {dialect_name}")


async def _toggle_relation(session: AsyncSession, model, user_id: int, post_id: int) -> None:
    """原子切换点赞或收藏关系，使并发双击不再触发唯一约束 500。"""

    statement = (
        _dialect_insert(session, model)
        .values(user_id=user_id, post_id=post_id)
        .on_conflict_do_nothing(index_elements=["user_id", "post_id"])
        .returning(model.id)
    )
    inserted_id = await session.scalar(statement)
    if inserted_id is None:
        await session.execute(
            delete(model).where(model.user_id == user_id, model.post_id == post_id)
        )


async def interaction_state(
    session: AsyncSession, post: Post, user_id: int | None
) -> PostInteractionState:
    """为文章详情页的互动操作区组装一次完整状态。

    用户需求：打开文章时需要看到浏览、点赞、收藏数量；登录用户还需要看到点赞和
    收藏按钮是否高亮。前端如果分别请求四五个接口，不仅慢，还容易出现数字与按钮
    状态来自不同时间点的问题，所以由这个函数统一查询并返回一个响应对象。

    调用入口：详情页读取互动状态、记录浏览完成后、点赞切换完成后、收藏切换完成后。
    执行逻辑：统计全站公开数量；有 ``user_id`` 时再查询这个用户与文章的关系是否存在；
    最后转换成 ``PostInteractionState``。本函数只读，不提交事务。
    """

    # select(func.count()) 会生成 COUNT(*)；select_from 指定统计哪张表；where 只保留当前文章。
    # scalar() 表示结果只需要一个标量数字，而不是一整行 ORM 对象。
    like_count = await session.scalar(
        select(func.count()).select_from(PostLike).where(PostLike.post_id == post.id)
    )
    favorite_count = await session.scalar(
        select(func.count()).select_from(PostFavorite).where(PostFavorite.post_id == post.id)
    )

    # 游客没有 user_id，无法查询“我是否点过”；先给出安全默认值 False。
    liked = favorited = False
    if user_id is not None:
        # 这里不需要加载整条记录，只查主键 ID。查到 ID 表示关系存在，所以 is not None 为 True。
        liked = (
            await session.scalar(
                select(PostLike.id).where(
                    PostLike.post_id == post.id,
                    PostLike.user_id == user_id,
                )
            )
            is not None
        )
        favorited = (
            await session.scalar(
                select(PostFavorite.id).where(
                    PostFavorite.post_id == post.id,
                    PostFavorite.user_id == user_id,
                )
            )
            is not None
        )

    # Schema 把多个数据库查询结果整理成稳定的 API 响应结构。
    # count() 正常返回整数；or 0 是防御性回退，避免驱动返回 None 时违反 Schema。
    return PostInteractionState(
        view_count=post.view_count,
        like_count=like_count or 0,
        favorite_count=favorite_count or 0,
        liked=liked,
        favorited=favorited,
    )


async def record_view(
    session: AsyncSession, post: Post, user_id: int | None
) -> PostInteractionState:
    """响应“用户打开文章详情”这个动作，并维护浏览量与个人足迹。

    用户需求有两层：所有访客打开详情页都应增加文章公开浏览数；已登录用户还应能在
    头像菜单的“我的足迹”中找到最近看过的文章。游客无法绑定身份，因此只累计总数。

    执行逻辑：原子增加 ``posts.view_count``；登录用户第一次阅读时新增一条 PostView，
    重复阅读时只更新原记录的 ``viewed_at``，从而既不产生重复足迹，又能把再次阅读的
    文章移到足迹顶部；提交后刷新计数，并返回详情页需要的完整互动状态。
    """

    # 使用数据库表达式原子自增，避免两个并发请求都读取旧值后相互覆盖。
    await session.execute(
        update(Post).where(Post.id == post.id).values(view_count=Post.view_count + 1)
    )
    if user_id is not None:
        now = datetime.now(UTC)
        # 单条 upsert 同时覆盖首次浏览和更新时间；唯一约束冲突由数据库内部处理，不会
        # 暴露成 IntegrityError，也不会回滚同一事务中的浏览量自增。
        await session.execute(
            _dialect_insert(session, PostView)
            .values(user_id=user_id, post_id=post.id, viewed_at=now)
            .on_conflict_do_update(
                index_elements=["user_id", "post_id"],
                set_={"viewed_at": now},
            )
        )

    # 浏览量和足迹在同一个事务中提交：都成功才生效；提交失败则都不会完成。
    await session.commit()
    # 上面的 view_count 是 SQL 表达式直接在数据库里 +1，当前 post 对象未必知道新值。
    # refresh() 只重新读取 view_count 字段，让返回给前端的数字一定是数据库最新值。
    await session.refresh(post, attribute_names=["view_count"])
    # 最后重新统计点赞/收藏状态，将完整详情操作区状态一次性返回。
    return await interaction_state(session, post, user_id)


async def toggle_like(session: AsyncSession, post: Post, user_id: int) -> PostInteractionState:
    """实现文章详情页同一个点赞按钮的“点赞/取消点赞”两种行为。

    用户第一次点击要建立点赞关系，已点赞后再次点击则要撤销，因此前端不必判断该调用
    新增还是删除，Service 以关系是否存在为最终依据。关系还会成为头像菜单“赞过”列表
    的数据来源。提交后重新统计并返回按钮高亮状态和数量，供前端立即更新界面。
    """

    await _toggle_relation(session, PostLike, user_id, post.id)
    # commit() 是事务边界；只有提交成功，其他请求才会看到这次变化。
    await session.commit()
    # 不手工猜测计数加一还是减一，重新查库可得到数据库的最终真实状态。
    return await interaction_state(session, post, user_id)


async def toggle_favorite(session: AsyncSession, post: Post, user_id: int) -> PostInteractionState:
    """实现文章详情页收藏按钮，并为用户保存以后可以重新找到的文章。

    收藏与点赞在交互上都是再次点击即可取消，但产品含义不同：点赞表达认可，收藏用于
    稍后阅读，所以分别持久化到 PostLike 和 PostFavorite。这里根据收藏关系是否存在执行
    新增或删除，提交后返回最新计数和高亮状态；收藏记录也供“我的收藏”列表查询。
    """

    await _toggle_relation(session, PostFavorite, user_id, post.id)
    await session.commit()
    return await interaction_state(session, post, user_id)


async def list_post_activity(
    session: AsyncSession, user_id: int, kind: str
) -> list[UserActivityItem]:
    """为“我的活动”页面的赞过、收藏、足迹三个标签页提供统一列表数据。

    用户需求：用户从头像 Popover 进入个人活动页后，可以切换“赞过”“收藏”“足迹”，
    找回自己点过赞、主动收藏或曾经浏览的文章。三个列表展示字段相同，区别仅在数据来源
    和“最近操作时间”，因此使用 ``kind`` 复用查询与响应组装，避免维护三份重复代码。

    执行逻辑：

    1. 把 Router 已校验的 ``kind`` 映射为点赞、收藏或足迹关系表；
    2. 从关系表筛选当前 ``user_id``，保证只展示本人活动；
    3. JOIN Post 是因为关系表只有 ``post_id``，页面还需要标题、发布时间和浏览量；
    4. 按点赞/收藏的 ``created_at`` 或足迹的 ``viewed_at`` 倒序，让最近操作置顶；
    5. 最多返回 100 条，并整理为三个标签都能复用的 ``UserActivityItem``。

    这里不能直接按文章发布时间排序：用户今天收藏一篇旧文章时，产品预期它出现在收藏
    列表顶部。函数只读取数据库，不会新增、修改或提交任何活动记录。
    """

    # Router 已用 Literal 限制 kind，只可能是这三个键；这里把字符串映射到真实 ORM 表。
    model = {"likes": PostLike, "favorites": PostFavorite, "views": PostView}[kind]
    # 足迹的时间字段叫 viewed_at，点赞和收藏都叫 created_at，因此需要按类型选择。
    activity_column = model.viewed_at if kind == "views" else model.created_at
    # JOIN 把行为表里的 post_id 与 posts.id 连接，才能同时拿到文章信息和操作时间。
    # desc() 按最近操作在前排列；id 再排序让时间相同时结果仍保持稳定。
    rows = (
        await session.execute(
            select(Post, activity_column)
            .join(model, model.post_id == Post.id)
            .where(model.user_id == user_id, Post.is_published.is_(True))
            .order_by(activity_column.desc(), model.id.desc())
            .limit(100)
        )
    ).all()
    # execute() 的每一行包含 (Post对象, 操作时间)，这里逐行转换成对外 Schema。
    return [
        UserActivityItem(
            id=post.id,
            title=post.title,
            created_at=post.created_at,
            activity_at=activity_at,
            view_count=post.view_count,
        )
        for post, activity_at in rows
    ]


async def list_comment_activity(
    session: AsyncSession, user_id: int
) -> list[UserCommentActivityItem]:
    """为“我的活动 -> 评论”列表找回当前用户曾经发表的评论。

    用户点击头像 Popover 中的“评论”后，需要同时看到自己的评论内容和它属于哪篇文章，
    并能通过 ``post_id`` 跳回文章详情。评论表不重复保存文章标题，所以查询时 JOIN Post；
    按评论发表时间倒序让最近评论置顶，最终返回前端列表需要的精简字段。本函数只读。
    """

    # 评论表只保存 post_id，不重复保存文章标题；JOIN posts 后才能在个人列表显示标题。
    rows = (
        await session.execute(
            select(Comment, Post.title)
            .join(Post, Post.id == Comment.post_id)
            .where(Comment.user_id == user_id, Post.is_published.is_(True))
            .order_by(Comment.created_at.desc(), Comment.id.desc())
            .limit(100)
        )
    ).all()
    return [
        UserCommentActivityItem(
            id=comment.id,
            content=comment.content,
            created_at=comment.created_at,
            post_id=comment.post_id,
            post_title=title,
        )
        for comment, title in rows
    ]
