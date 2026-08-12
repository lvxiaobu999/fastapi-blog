"""按帖子管理评论 WebSocket 连接。

连接只保存在当前应用进程内；数据库持久化由评论 Service 负责。
"""

import asyncio
from collections import defaultdict

from fastapi import WebSocket


class CommentConnectionManager:
    """维护文章房间，并向同一文章的在线客户端广播 JSON。"""

    def __init__(self) -> None:
        # key 是 post_id，value 是正在查看该帖子的 WebSocket 集合；这就是“房间”。
        self._rooms: dict[int, set[WebSocket]] = defaultdict(set)
        # 多个协程可能同时连接、断开和广播，Lock 只保护内存集合，不包围网络发送。
        self._lock = asyncio.Lock()

    async def register(self, post_id: int, websocket: WebSocket) -> None:
        """把已经完成认证的连接加入对应帖子房间。"""

        async with self._lock:
            self._rooms[post_id].add(websocket)

    async def disconnect(self, post_id: int, websocket: WebSocket) -> None:
        """移除断开的连接，并清理空房间。"""

        async with self._lock:
            room = self._rooms.get(post_id)
            if room is None:
                return
            room.discard(websocket)
            if not room:
                self._rooms.pop(post_id, None)

    async def broadcast(self, post_id: int, message: dict[str, object]) -> None:
        """广播消息；发送失败的连接会从房间中移除。"""

        async with self._lock:
            # 复制快照后立即释放锁。send_json() 可能等待网络，不能在等待期间阻塞
            # 其他用户加入或离开房间。
            sockets = tuple(self._rooms.get(post_id, ()))

        async def send(socket: WebSocket) -> WebSocket | None:
            """限制单连接发送时间，避免慢客户端拖住整个房间。"""

            try:
                async with asyncio.timeout(2):
                    await socket.send_json(message)
                return None
            except (RuntimeError, TimeoutError):
                return socket

        stale = [
            socket
            for socket in await asyncio.gather(*(send(socket) for socket in sockets))
            if socket is not None
        ]
        for socket in stale:
            await self.disconnect(post_id, socket)


comment_connections = CommentConnectionManager()
