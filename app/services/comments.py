"""评论查询与写入服务；不处理 HTTP/WebSocket 连接或广播。"""

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Comment
from app.schemas.comment import CommentCreate


class CommentParentNotFoundError(ValueError):
    """回复目标不存在，或目标不属于当前帖子。"""


async def list_comments(session: AsyncSession, post_id: int) -> list[Comment]:
    """按时间正序返回帖子的历史评论，并预加载渲染所需关系。"""

    # joinedload 在同一次查询链中准备评论作者和“被回复评论的作者”。如果不预加载，
    # Router 序列化 reply_to_author 时可能在 AsyncSession 外触发隐式查询并报错。
    rows = await session.scalars(
        select(Comment)
        .options(joinedload(Comment.author), joinedload(Comment.parent).joinedload(Comment.author))
        .where(Comment.post_id == post_id)
        .order_by(Comment.created_at.asc(), Comment.id.asc())
    )
    return list(rows)


async def create_comment(
    session: AsyncSession, post_id: int, user_id: int, data: CommentCreate
) -> Comment:
    """校验回复关系，在单次事务中保存评论，并加载广播所需信息。"""

    # 第一步：没有 parent_id 就是顶级评论；有 parent_id 才需要查询回复目标。
    parent = None
    root_id = None
    if data.parent_id is not None:
        # 第二步：查询条件同时包含 Comment.id 和 post_id，禁止回复其他帖子里的评论。
        parent = await session.scalar(
            select(Comment)
            .options(joinedload(Comment.author))
            .where(Comment.id == data.parent_id, Comment.post_id == post_id)
        )
        if parent is None:
            raise CommentParentNotFoundError
        # 第三步：回复顶级评论时 parent.root_id 为空，使用 parent.id；回复另一条回复时
        # 沿用它已有的 root_id。所有回复因此都能平铺到同一个顶级讨论串下面。
        root_id = parent.root_id or parent.id

    # 第四步：作者 ID 来自已经认证的 user_id；root_id 不接受前端直接指定。
    comment = Comment(
        post_id=post_id,
        user_id=user_id,
        content=data.content,
        parent_id=parent.id if parent else None,
        root_id=root_id,
    )

    print({k: v for k, v in comment.__dict__.items() if not k.startswith("_")})
    session.add(comment)
    try:
        # 第五步：必须先提交数据库，再允许 WebSocket Router 广播。否则提交失败时，
        # 其他用户会短暂看到刷新后消失的“假评论”。
        await session.commit()
        # 第六步：重新查询并预加载作者关系，保证返回对象可直接交给 Pydantic 序列化。
        comment = await session.scalar(
            select(Comment)
            .options(
                joinedload(Comment.author),
                joinedload(Comment.parent).joinedload(Comment.author),
            )
            .where(Comment.id == comment.id)
        )
        print({k: v for k, v in comment.__dict__.items() if not k.startswith("_")})
    except Exception:
        # 已处理的写入异常必须回滚，避免这个 Session 停留在失败事务中。
        await session.rollback()
        raise
    assert comment is not None
    return comment
