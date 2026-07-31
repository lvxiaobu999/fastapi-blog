"""文章互动和个人活动查询服务。

所有写操作都使用 Router 注入的 AsyncSession，并在单个用户动作内集中提交。
"""

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Comment, Post, PostFavorite, PostLike, PostView
from app.schemas.post import (
    PostInteractionState,
    UserActivityItem,
    UserCommentActivityItem,
)


async def interaction_state(
    session: AsyncSession, post: Post, user_id: int | None
) -> PostInteractionState:
    """汇总文章公开计数，并在已登录时返回当前用户的操作状态。"""

    # select(func.count()) 会生成 COUNT(*)；select_from 指定统计哪张表；where 只保留当前文章。
    # scalar() 表示结果只需要一个标量数字，而不是一整行 ORM 对象。
    like_count = await session.scalar(
        select(func.count()).select_from(PostLike).where(PostLike.post_id == post.id)
    )
    favorite_count = await session.scalar(
        select(func.count())
        .select_from(PostFavorite)
        .where(PostFavorite.post_id == post.id)
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


async def record_view(session: AsyncSession, post: Post, user_id: int | None) -> PostInteractionState:
    """每次详情页加载增加一次浏览量；登录用户同时更新最近足迹。"""

    # 使用数据库表达式原子自增，避免两个并发请求都读取旧值后相互覆盖。
    await session.execute(
        update(Post).where(Post.id == post.id).values(view_count=Post.view_count + 1)
    )
    if user_id is not None:
        # 这段查询是在问：post_views 表里是否已经有“这个用户 + 这篇文章”的一行。
        # scalar(select(PostView)) 返回 ORM 对象；没有匹配行时返回 None。
        footprint = await session.scalar(
            select(PostView).where(
                PostView.user_id == user_id,
                PostView.post_id == post.id,
            )
        )
        if footprint is None:
            # 第一次浏览：内存中新建 PostView 并交给 Session 跟踪。
            # add() 此时还没有提交数据库，真正写入发生在下面的 commit()。
            session.add(PostView(user_id=user_id, post_id=post.id))
        else:
            # 再次浏览：不新增重复足迹，只修改已查到对象的最近浏览时间。
            # ORM 发现属性变化后，会在 commit() 时自动生成 UPDATE SQL。
            footprint.viewed_at = datetime.now(UTC)

    # 浏览量和足迹在同一个事务中提交：都成功才生效；提交失败则都不会完成。
    await session.commit()
    # 上面的 view_count 是 SQL 表达式直接在数据库里 +1，当前 post 对象未必知道新值。
    # refresh() 只重新读取 view_count 字段，让返回给前端的数字一定是数据库最新值。
    await session.refresh(post, attribute_names=["view_count"])
    # 最后重新统计点赞/收藏状态，将完整详情操作区状态一次性返回。
    return await interaction_state(session, post, user_id)


async def toggle_like(session: AsyncSession, post: Post, user_id: int) -> PostInteractionState:
    """切换点赞关系并返回最新计数。"""

    # 先查询关系是否存在：存在代表当前已点赞，不存在代表当前未点赞。
    row = await session.scalar(
        select(PostLike).where(
            PostLike.user_id == user_id,
            PostLike.post_id == post.id,
        )
    )
    if row is None:
        # 未点赞 -> 新增关系。user_id 来自认证结果，不能由前端冒充指定。
        session.add(PostLike(user_id=user_id, post_id=post.id))
    else:
        # 已点赞 -> 删除查到的关系，也就是“取消点赞”。
        await session.delete(row)
    # commit() 是事务边界；只有提交成功，其他请求才会看到这次变化。
    await session.commit()
    # 不手工猜测计数加一还是减一，重新查库可得到数据库的最终真实状态。
    return await interaction_state(session, post, user_id)


async def toggle_favorite(session: AsyncSession, post: Post, user_id: int) -> PostInteractionState:
    """切换收藏关系并返回最新计数。"""

    # 收藏与点赞使用同一种“有则删除、无则新增”的切换逻辑，只是操作不同表。
    row = await session.scalar(
        select(PostFavorite).where(
            PostFavorite.user_id == user_id,
            PostFavorite.post_id == post.id,
        )
    )
    if row is None:
        # 第一次点击收藏。
        session.add(PostFavorite(user_id=user_id, post_id=post.id))
    else:
        # 再次点击取消收藏。
        await session.delete(row)
    await session.commit()
    return await interaction_state(session, post, user_id)


async def list_post_activity(session: AsyncSession, user_id: int, kind: str) -> list[UserActivityItem]:
    """按最近操作时间查询点赞、收藏或足迹文章。"""

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
            .where(model.user_id == user_id)
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


async def list_comment_activity(session: AsyncSession, user_id: int) -> list[UserCommentActivityItem]:
    """查询用户最近发表的评论，并携带所属文章标题。"""

    # 评论表只保存 post_id，不重复保存文章标题；JOIN posts 后才能在个人列表显示标题。
    rows = (
        await session.execute(
            select(Comment, Post.title)
            .join(Post, Post.id == Comment.post_id)
            .where(Comment.user_id == user_id)
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
