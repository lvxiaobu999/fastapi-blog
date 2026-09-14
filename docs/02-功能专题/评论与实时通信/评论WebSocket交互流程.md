# 评论 WebSocket 交互流程

本文只聚焦评论 WebSocket 的连接生命周期，回答以下问题：用户在哪里进入帖子房间，何时触发 `register()`，评论在哪里触发 `broadcast()`，以及连接关闭后在哪里触发 `disconnect()`。

## 1. 先看完整入口

评论详情页加载 `app/static/js/comments.js` 后，在文件末尾并行执行：

```javascript
loadHistory();
connect();
```

- `loadHistory()` 使用 HTTP 获取已经存在的评论。
- `connect()` 创建 WebSocket，接收页面打开后的实时评论。

前端连接地址为：

```text
WS /api/posts/{post_id}/comments/ws
```

例如用户打开帖子 12，浏览器会连接：

```text
ws://127.0.0.1:8000/api/posts/12/comments/ws
```

这里的 `post_id=12` 就是房间编号。客户端后续不能通过消息修改房间编号。

## 2. 用户怎样进入房间并触发 register

### 2.1 前端创建连接

`app/static/js/comments.js` 的 `connect()` 创建 WebSocket，并绑定四个浏览器事件：

```javascript
socket = new WebSocket(`${protocol}//${window.location.host}/api/posts/${postId}/comments/ws`);
socket.addEventListener("open", handleSocketOpen);
socket.addEventListener("message", handleSocketMessage);
socket.addEventListener("close", handleSocketClose);
socket.addEventListener("error", handleSocketError);
```

浏览器握手成功后触发 `open`。`handleSocketOpen()` 随即发送第一条认证消息：

```json
{"type": "authenticate", "token": "当前 access token"}
```

### 2.2 后端接受连接并认证

`app/routers/comments.py` 的 `comment_websocket()` 是后端入口：

```python
await websocket.accept()
```

`accept()` 只表示 WebSocket 网络握手已经接受，不表示用户已经登录，也不表示连接已经进入评论房间。

Router 接下来依次执行：

1. 根据 URL 的 `post_id` 检查帖子是否存在。
2. `_authenticate_websocket()` 等待首条消息，校验消息格式和 Access Token。
3. 使用 Token 中的用户 ID 查询数据库，确认用户仍然存在。
4. 认证成功后调用 `comment_connections.register(post_id, websocket)`。
5. 向该连接发送 `{"type": "authenticated"}`。

因此，真正的入房间调用点是：

```python
await comment_connections.register(post_id, websocket)
```

`app/websockets/comments.py` 的 `register()` 会把连接加入内存字典：

```python
self._rooms[post_id].add(websocket)
```

房间结构可以理解为：

```text
{
    12: {用户 A 的 WebSocket, 用户 B 的 WebSocket},
    20: {用户 C 的 WebSocket}
}
```

只有认证成功的连接会进入房间。帖子不存在、认证消息错误、Token 失效或用户不存在时，后端会关闭连接，不会调用 `register()`。

### 2.3 入房间时序

```text
浏览器 comments.js             Router comments.py             房间管理器
       |                              |                           |
       |-- new WebSocket() ---------->|                           |
       |                              |-- accept()                |
       |<-- open 事件 ----------------|                           |
       |-- authenticate + token ----->|                           |
       |                              |-- 校验帖子、Token、用户    |
       |                              |-- register(post_id, ws) -->|
       |                              |                           |-- 加入 _rooms[post_id]
       |<-- authenticated ------------|                           |
       |-- 启用评论输入框              |                           |
```

## 3. 评论在哪里触发 broadcast

用户提交表单后，`handleCommentSubmit()` 发送：

```json
{
  "type": "comment.create",
  "content": "评论正文",
  "parent_id": null
}
```

回复其他评论时，`parent_id` 是被回复评论的 ID。作者 ID 来自已经认证的用户，帖子 ID 来自 WebSocket URL，客户端不能自行指定。

后端已经进入 `while True` 接收循环：

```text
receive_json()
  -> CommentCreateMessage 校验
  -> comment_service.create_comment()
  -> INSERT / COMMIT / REFRESH
  -> CommentResponse
```

Service 提交数据库事务成功后，Router 才执行两次发送：

```python
await websocket.send_json({"type": "comment.submitted"})
await comment_connections.broadcast(
    post_id,
    {"type": "comment.created", "data": ...},
)
```

两条消息用途不同：

| 消息 | 接收者 | 用途 |
| --- | --- | --- |
| `comment.submitted` | 只有提交评论的连接 | 解除提交按钮 loading，清空输入框 |
| `comment.created` | 当前帖子房间的全部连接，包含提交者 | 使用同一个入口把新评论渲染到页面 |

所以，触发广播的唯一正常业务调用点位于 `app/routers/comments.py` 的 `comment_websocket()` 中。它发生在评论成功持久化之后。校验失败、父评论不存在或数据库写入失败时不会广播。

`app/websockets/comments.py` 的 `broadcast()` 先复制房间连接快照，再逐个执行 `send_json()`。复制快照后释放锁，是为了避免慢网络发送阻塞其他用户进入或退出房间。

### 评论与广播时序

```text
用户 A              Router             Service / 数据库        房间 12              用户 B
  |                    |                       |                   |                    |
  |-- comment.create ->|                       |                   |                    |
  |                    |-- 校验消息            |                   |                    |
  |                    |-- create_comment() -->|                   |                    |
  |                    |                       |-- INSERT/COMMIT   |                    |
  |                    |<-- 已保存的评论 -------|                   |                    |
  |<-- submitted ------|                       |                   |                    |
  |                    |-- broadcast(created) -------------------->|                    |
  |<-- comment.created --------------------------------------------|                    |
  |                    |                       |                   |-- comment.created ->|
  |-- 渲染评论          |                       |                   |          渲染评论 --|
```

必须保持“数据库提交成功，再广播”的顺序，否则客户端可能看到刷新后消失的评论。

## 4. 连接在哪里断开并触发 disconnect

### 4.1 页面正常关闭或刷新

浏览器执行 `beforeunload`：

```javascript
closedByPage = true;
window.clearTimeout(reconnectTimer);
socket?.close();
```

`socket.close()` 关闭客户端连接。后端正在等待消息的 `receive_json()` 感知关闭并抛出 `WebSocketDisconnect`，随后一定进入 `finally`：

```python
finally:
    if registered:
        await comment_connections.disconnect(post_id, websocket)
```

`registered` 防止未完成认证、从未加入房间的连接执行无意义清理。

### 4.2 网络异常、服务器关闭或接收异常

网络中断时，浏览器不一定来得及执行 `beforeunload`。后端的 `receive_json()` 仍会因连接丢失抛出异常，然后进入同一个 `finally`，因此清理入口没有区别。

前端收到 `close` 事件后执行 `handleSocketClose()`：

- 解除可能残留的提交 loading。
- 禁用评论输入框和提交按钮。
- 页面没有主动关闭、Token 仍存在时，最多尝试重连 3 次。
- 重连会创建一个新连接，重新认证并重新 `register()`。

### 4.3 广播时发现失效连接

还有一条兜底清理路径：某个连接虽然仍在房间集合中，但 `broadcast()` 调用 `send_json()` 时已经失效。管理器会把它加入 `stale` 列表，广播循环结束后调用：

```python
await self.disconnect(post_id, socket)
```

因此 `disconnect()` 有两个触发来源：

| 来源 | 调用位置 | 场景 |
| --- | --- | --- |
| Router 的 `finally` | `app/routers/comments.py` | 页面关闭、刷新、网络中断或消息接收退出 |
| Manager 的广播清理 | `app/websockets/comments.py` | 向房间连接发送消息时发现连接已经失效 |

`disconnect()` 使用 `discard()` 移除连接。如果房间已经没有连接，还会删除整个 `post_id` 键，避免空房间长期占用内存。

### 断开时序

```text
浏览器                      Router                         房间管理器
  |                            |                              |
  |-- close() / 网络断开 ------>|                              |
  |                            |-- receive_json() 退出         |
  |                            |-- 捕获 WebSocketDisconnect    |
  |                            |-- 进入 finally                |
  |                            |-- disconnect(post_id, ws) --->|
  |                            |                              |-- 移除连接
  |                            |                              |-- 空房间则删除
  |<-- close 事件 -------------|                              |
  |-- 禁用表单或安排重连        |                              |
```

## 5. 一条连接的后端主线

阅读 `comment_websocket()` 时，可以先忽略辅助函数细节，只记住下面的主线：

```python
accept()                         # 接受网络连接，尚未认证
try:
    检查帖子
    读取首条 authenticate 消息
    验证 Token 和数据库用户
    register(post_id, websocket) # 认证成功，进入帖子房间
    发送 authenticated

    while True:
        接收 comment.create
        校验并写入数据库
        向提交者发送 comment.submitted
        broadcast(comment.created)  # 广播给房间全部连接
finally:
    disconnect(post_id, websocket)  # 只清理已经注册的连接
```

五个概念不能混在一起：

| 操作 | 含义 |
| --- | --- |
| `new WebSocket()` | 浏览器开始建立连接 |
| `accept()` | 服务端接受网络握手 |
| 认证 | 服务端确认连接对应哪个用户 |
| `register()` | 把已认证连接加入某个帖子的应用层房间 |
| `disconnect()` | 从应用层房间移除连接，并在需要时删除空房间 |

## 6. 调试时按这里打断点

建议按以下顺序观察一条真实连接：

1. `app/static/js/comments.js` 的 `connect()`：确认页面是否创建连接、URL 中的 `postId` 是否正确。
2. `handleSocketOpen()`：确认浏览器是否发送 `authenticate`。
3. `app/routers/comments.py` 的 `comment_websocket()`：观察 `accept()`、认证结果和 `register()`。
4. `app/websockets/comments.py` 的 `register()`：查看 `_rooms[post_id]` 中的连接数量。
5. Router 的 `while True`：观察收到的 `comment.create`。
6. `comment_service.create_comment()` 返回位置：确认事务已经提交。
7. Manager 的 `broadcast()`：查看房间快照和逐连接发送。
8. Router 的 `finally` 与 Manager 的 `disconnect()`：刷新页面，确认旧连接被移除。

浏览器开发者工具的 Network 页面选择 WS 连接后，可以在 Messages/Frames 中依次看到：

```text
发送 authenticate
接收 authenticated
发送 comment.create
接收 comment.submitted
接收 comment.created
```

## 7. 当前实现的边界

房间存储在当前 Python 进程的内存中。单个 Uvicorn Worker 可以正常向同房间广播；多个 Worker 或多个服务实例之间不能共享 `_rooms`。生产环境扩展到多进程后，需要 Redis Pub/Sub 等消息总线把评论事件同步到每个进程，再由各进程向自己持有的 WebSocket 连接发送。

WebSocket 只推送页面打开后的增量消息。当前重连后不会自动补齐断线期间遗漏的评论；刷新页面或重新调用历史评论 HTTP 接口才会重新获得完整状态。
