"""评论历史 HTTP 接口与实时 WebSocket 端点。"""

import asyncio
from typing import Annotated

import jwt
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_responses import success_response
from app.db.session import get_db
from app.models import Post, User
from app.schemas.api import ApiSuccess
from app.schemas.comment import (
    CommentCreateMessage,
    CommentResponse,
    WebSocketAuthMessage,
)
from app.services import comments as comment_service
from app.services.auth import verify_access_token
from app.websockets.comments import comment_connections

router = APIRouter(prefix="/api/posts/{post_id}/comments", tags=["comments"])
DbSession = Annotated[AsyncSession, Depends(get_db)]
AUTH_TIMEOUT_SECONDS = 10


async def _send_websocket_error(websocket: WebSocket, message: str, *, close: bool = False) -> None:
    """发送统一错误消息；不可恢复错误同时用 1008 关闭连接。"""

    await websocket.send_json({"type": "error", "message": message})
    if close:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)


async def _authenticate_websocket(websocket: WebSocket, session: AsyncSession) -> User | None:
    """读取首条认证消息并返回数据库用户；失败时通知客户端并关闭连接。"""

    # WebSocket 握手只建立网络连接，不能证明用户身份。限制首消息等待时间，避免
    # 未认证客户端永久占用服务器连接。
    try:
        async with asyncio.timeout(AUTH_TIMEOUT_SECONDS):
            raw_message = await websocket.receive_json()
        auth_message = WebSocketAuthMessage.model_validate(raw_message)
    except TimeoutError:
        await _send_websocket_error(websocket, "认证超时，请重新连接", close=True)
        return None
    except ValidationError:
        await _send_websocket_error(websocket, "请先登录后评论", close=True)
        return None

    # Token 放在首条消息而不是 URL 查询参数中，避免进入代理访问日志。JWT 验证后
    # 仍然查数据库，确保用户没有被删除，且后续作者身份以服务端结果为准。
    try:
        user_id = verify_access_token(auth_message.token)
    except (jwt.PyJWTError, TypeError, ValueError):
        await _send_websocket_error(websocket, "登录状态已失效", close=True)
        return None
    user = await session.get(User, user_id)
    if user is None:
        await _send_websocket_error(websocket, "登录状态已失效", close=True)
    return user


async def _create_comment_from_message(
    websocket: WebSocket,
    session: AsyncSession,
    post_id: int,
    user: User,
) -> CommentResponse | None:
    """接收并保存一条评论；可恢复的协议或业务错误不会断开连接。"""

    raw_message = await websocket.receive_json()
    try:
        message = CommentCreateMessage.model_validate(raw_message)
    except ValidationError:
        await _send_websocket_error(websocket, "评论不能为空且不能超过 1000 个字符")
        return None

    try:
        comment = await comment_service.create_comment(session, post_id, user.id, message)
    except comment_service.CommentParentNotFoundError:
        await _send_websocket_error(websocket, "回复的评论不存在")
        return None
    return CommentResponse.model_validate(comment)


@router.get("", response_model=ApiSuccess[list[CommentResponse]])
async def list_comments(
    request: Request, post_id: int, session: DbSession
) -> ApiSuccess[list[CommentResponse]]:
    """公开读取指定帖子的历史评论。"""

    if await session.get(Post, post_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    comments = await comment_service.list_comments(session, post_id)
    return success_response(
        request, [CommentResponse.model_validate(comment) for comment in comments]
    )


@router.websocket("/ws")
async def comment_websocket(websocket: WebSocket, post_id: int, session: DbSession) -> None:
    """按“接受 → 认证 → 入房间 → 收消息 → 广播 → 清理”管理连接。"""

    await websocket.accept()
    registered = False
    try:
        # URL 决定这条连接属于哪个帖子房间，不接受客户端在消息里更改 post_id。
        if await session.get(Post, post_id) is None:
            await _send_websocket_error(websocket, "帖子不存在", close=True)
            return

        user = await _authenticate_websocket(websocket, session)
        if user is None:
            return

        # 只有认证成功的连接才能进入房间并接收其他用户的实时评论。
        await comment_connections.register(post_id, websocket)
        registered = True
        await websocket.send_json({"type": "authenticated"})

        while True:
            comment = await _create_comment_from_message(websocket, session, post_id, user)
            if comment is None:
                continue
            # Service 已经提交事务；此时广播能保证页面收到的评论刷新后仍然存在。
            # submitted 只发给提交者，用于解除发送按钮的 loading 锁；随后 created
            # 才广播给房间内所有客户端，避免提交者在本地重复插入评论。
            await websocket.send_json({"type": "comment.submitted"})
            await comment_connections.broadcast(
                post_id,
                {"type": "comment.created", "data": comment.model_dump(mode="json")},
            )
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        if registered:
            await comment_connections.disconnect(post_id, websocket)
