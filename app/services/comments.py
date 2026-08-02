"""评论查询与写入服务；不处理 HTTP/WebSocket 连接或广播。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Comment
from app.schemas.comment import CommentCreate

# ==================== Service 入口导读 ====================
# 上游调用者：comments Router 的历史 HTTP 接口和 WebSocket comment.create 消息处理。
# 本模块负责评论查询、回复目标校验、root/parent 关系计算和事务；不负责连接或广播。
# 评论必须 commit 成功后 Router 才能 broadcast，避免其他用户收到数据库中不存在的评论。
# 写入异常会 rollback，使同一个 AsyncSession 不会停留在失败事务状态。


class CommentParentNotFoundError(ValueError):
    """回复目标不存在，或目标不属于当前帖子。"""


async def list_comments(session: AsyncSession, post_id: int) -> list[Comment]:
    """让新进入文章详情页的用户先看到已经存在的完整评论讨论。

    WebSocket 只能推送连接建立之后的新评论，不能代替历史数据查询，所以页面初始化先调用
    此函数。结果包含顶级评论和回复，按时间正序供前端依据 ``root_id`` 组装讨论串；同时
    预加载评论作者及被回复者，让界面能显示“回复某某”。本函数只读，不负责注册房间。
    """

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
    """把用户通过 WebSocket 发送的顶级评论或相互回复可靠地保存下来。

    用户需求：一篇文章下有多条顶级评论；每条顶级评论下可以有多条回复，并且回复之间
    可以继续相互回复。``parent_id`` 保存“实际回复谁”，用于显示被回复者；``root_id``
    保存“属于哪个顶级讨论串”，用于把所有层级回复平铺在同一个回复区，形成掘金式效果。

    执行时先确认回复目标属于当前文章，再由后端计算 root_id，禁止客户端伪造讨论归属；
    保存并提交成功后加载广播所需的作者关系。Router 只有拿到成功结果才向房间广播，避免
    其他在线用户收到数据库中并不存在的评论。失败会 rollback，函数本身不管理连接。
    """

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
    except Exception:
        # 已处理的写入异常必须回滚，避免这个 Session 停留在失败事务中。
        await session.rollback()
        raise
    assert comment is not None
    return comment
