# WebSocket 通信与实时评论

本文结合当前博客的评论功能，解释 WebSocket 的基本概念、FastAPI 后端实现、浏览器前端实现，以及两端如何完成认证、发送评论和实时广播。

## 1. WebSocket 解决什么问题

普通 HTTP 是一次请求对应一次响应：

```text
浏览器 -- GET /comments --> 服务端
浏览器 <-- 评论列表 -------- 服务端
连接结束
```

如果浏览器想及时知道其他用户是否发表了评论，只使用 HTTP 就需要不断轮询：

```text
每隔 3 秒请求一次评论列表
```

这会产生大量没有新数据的请求，而且实时性取决于轮询间隔。

WebSocket 在 HTTP 握手成功后把连接升级为长连接。连接建立后，浏览器和服务端都能主动发送消息：

```text
浏览器 <==============================> FastAPI
           一条持续存在的双向连接
```

因此，当用户 A 发表评论后，服务端可以立即把新评论推送给正在查看同一帖子的用户 B，而不需要用户 B 主动刷新。

## 2. 当前项目为什么同时使用 HTTP 和 WebSocket

评论功能没有把所有工作都放进 WebSocket：

| 工作 | 使用方式 | 原因 |
| --- | --- | --- |
| 首次读取历史评论 | `GET /api/posts/{post_id}/comments` | HTTP 适合可缓存、可重试的一次性查询 |
| 建立实时通道 | `WS /api/posts/{post_id}/comments/ws` | 维持双向长连接 |
| 提交新评论 | WebSocket `comment.create` | 提交后可以在同一通道广播 |
| 接收其他用户评论 | WebSocket `comment.created` | 服务端主动推送，不需要轮询 |

这种组合可以概括为：

```text
HTTP 获取当前状态 + WebSocket 接收后续增量
```

即使 WebSocket 暂时断开，历史评论接口仍然可以正常读取。WebSocket 不代替数据库，也不适合承担所有普通 CRUD 请求。

## 3. 涉及的项目文件

| 文件 | 职责 |
| --- | --- |
| `app/routers/comments.py` | HTTP 历史接口、WebSocket 生命周期、认证和消息分发 |
| `app/websockets/comments.py` | 按帖子管理在线连接并广播消息 |
| `app/schemas/comment.py` | 校验客户端消息并过滤公开响应字段 |
| `app/services/comments.py` | 查询回复目标、计算评论层级、提交事务 |
| `app/models/comment.py` | 评论表及作者、帖子、父评论关系 |
| `app/static/js/comments.js` | 浏览器连接、认证、发送、接收、重连和 DOM 更新 |
| `app/templates/post.html` | 评论列表与输入区域的 HTML 容器 |
| `tests/test_comments.py` | Service、HTTP 页面和 WebSocket 协议测试 |

正常调用方向是：

```text
浏览器 WebSocket
  -> Router 校验协议与身份
  -> Pydantic Schema 校验消息
  -> Service 执行业务和事务
  -> SQLAlchemy 写入数据库
  -> ConnectionManager 广播
  -> 浏览器更新评论区
```

### 推荐阅读代码的顺序

第一次阅读时不要从所有文件同时开始，建议沿一条评论的流向阅读：

1. 先看 `app/templates/post.html`，确认页面为评论列表和输入框提供了哪些 DOM 容器。
2. 看 `app/static/js/comments.js` 最后的 `loadHistory()` 和 `connect()`，找到前端启动入口。
3. 顺着 `connect()` 阅读 `handleSocketOpen()`、`handleSocketMessage()`、`handleSocketClose()`，理解浏览器连接生命周期。
4. 看 `app/schemas/comment.py` 中的 `WebSocketAuthMessage` 和 `CommentCreateMessage`，确认消息协议允许哪些字段。
5. 看 `app/routers/comments.py` 的 `comment_websocket()`，先只读它的“接受、认证、注册、循环、清理”主线。
6. 再进入 `_authenticate_websocket()` 和 `_create_comment_from_message()`，分别理解认证和单条消息处理。
7. 看 `app/services/comments.py`，理解数据库事务及 `parent_id/root_id` 的计算。
8. 最后看 `app/websockets/comments.py`，理解房间只是如何保存连接和广播，不参与数据库业务。

整理后的后端主函数刻意保持接近下面的伪代码：

```python
接受连接
try:
    检查帖子
    认证用户
    注册到帖子房间
    while True:
        接收并保存一条评论
        广播已经保存的评论
finally:
    从房间移除连接
```

复杂细节被拆到命名明确的辅助函数中，主函数用于回答“整条连接按什么顺序运行”，辅助函数用于回答“其中一步具体怎么做”。

## 4. WebSocket 地址

当前端点定义在 `app/routers/comments.py`：

```python
@router.websocket("/ws")
async def comment_websocket(
    websocket: WebSocket,
    post_id: int,
    session: DbSession,
) -> None:
    ...
```

Router 前缀是：

```text
/api/posts/{post_id}/comments
```

所以完整地址是：

```text
ws://localhost:8000/api/posts/123/comments/ws
```

HTTPS 页面必须使用加密的 `wss://`：

```text
https://blog.example.com -> wss://blog.example.com/api/posts/123/comments/ws
```

浏览器不能从 HTTPS 页面连接不安全的 `ws://`，否则会被混合内容策略拦截。

## 5. 后端连接生命周期

### 5.1 接受连接

客户端发起 WebSocket 握手后，FastAPI 必须显式接受：

```python
await websocket.accept()
```

此时只代表网络连接已建立，并不代表用户已经通过业务认证。

### 5.2 检查帖子

服务端先确认 URL 中的 `post_id` 对应真实帖子。帖子不存在时发送错误并使用 `1008` 关闭：

```python
await websocket.send_json({"type": "error", "message": "帖子不存在"})
await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
```

WebSocket 关闭码不是 HTTP 状态码。握手升级完成后，后续错误需要通过 WebSocket 消息和关闭码表达。

### 5.3 首消息认证

浏览器建立连接后必须在 10 秒内发送：

```json
{
  "type": "authenticate",
  "token": "<access-token>"
}
```

后端验证 JWT，再使用 Token 中的用户 ID 查询数据库用户。成功后才把连接注册到文章房间，并返回：

```json
{
  "type": "authenticated"
}
```

Token 没有放在 WebSocket URL 查询参数中，是为了减少它进入反向代理访问日志、浏览器历史或监控记录的机会。WebSocket 建立后不能像普通 AJAX 那样自由设置 `Authorization` Header，因此本项目选择“首消息认证”。

### 5.4 持续接收消息

认证成功后，Router 进入循环：

```python
while True:
    message = await websocket.receive_json()
```

`await` 不会持续占用 CPU。当前协程会暂停，事件循环可以继续处理其他 HTTP 请求和 WebSocket 连接；只有收到消息后该协程才继续执行。

浏览器关闭页面或网络断开时，`receive_json()` 会抛出 `WebSocketDisconnect`。`finally` 块负责把连接从房间移除，避免保存失效连接。

## 6. 消息协议

WebSocket 只提供传输通道，不会自动规定 JSON 字段。`type`、`content`、`data` 都是本项目自行设计的应用层协议。

### 6.1 客户端认证

```json
{
  "type": "authenticate",
  "token": "eyJ..."
}
```

### 6.2 认证成功

```json
{
  "type": "authenticated"
}
```

### 6.3 发表顶级评论

```json
{
  "type": "comment.create",
  "content": "这篇文章很有帮助",
  "parent_id": null
}
```

### 6.4 回复某条评论

```json
{
  "type": "comment.create",
  "content": "我同意你的观点",
  "parent_id": 42
}
```

客户端只提交实际回复目标 `parent_id`，不能提交作者 ID、`post_id` 或 `root_id`：

- 作者从已经验证的 JWT 用户取得。
- `post_id` 从 WebSocket URL 取得。
- `root_id` 由 Service 查询父评论后计算。

### 6.5 服务端广播新评论

评论写入数据库后，服务端先只向提交者返回确认消息：

```json
{
  "type": "comment.submitted"
}
```

前端收到它后解除“发送中”按钮锁并清空输入。该消息不广播给其他用户；它只确认当前连接提交的评论已经持久化。

随后服务端向整个帖子房间广播：

```json
{
  "type": "comment.created",
  "data": {
    "id": 51,
    "content": "我同意你的观点",
    "created_at": "2026-07-29T10:30:00Z",
    "post_id": 12,
    "parent_id": 42,
    "root_id": 38,
    "author": {
      "id": 7,
      "nickname": "读者",
      "image_path": "/media/profile_pics/avatar.png"
    },
    "reply_to_author": {
      "id": 9,
      "nickname": "另一位读者",
      "image_path": "/static/images/default.jpg"
    }
  }
}
```

### 6.6 业务错误

```json
{
  "type": "error",
  "message": "回复的评论不存在"
}
```

可恢复错误只发送 `error`，连接继续存在。例如内容校验失败。认证失败或帖子不存在属于连接无法继续工作的错误，服务端发送错误后关闭连接。

## 7. Pydantic 与 Service 的作用

Router 收到 `comment.create` 后通过 `CommentCreate` 校验：

```python
data = CommentCreate.model_validate(message)
```

它负责：

- 评论正文长度为 1 到 1000。
- 去掉正文首尾空白。
- 拒绝只包含空格的评论。
- `parent_id` 如果存在，必须是正整数。

Service 随后处理数据库规则：

```text
parent_id 为空
  -> 创建顶级评论，root_id 为空

parent_id 不为空
  -> 查询父评论
  -> 确认父评论属于当前帖子
  -> root_id = 父评论.root_id 或 父评论.id
  -> 创建回复
```

评论必须先 `commit()` 成功，Router 才会广播。这样客户端收到的 `comment.created` 一定对应已经持久化的数据；如果数据库提交失败，不会出现刷新页面后消失的“假评论”。

## 8. 连接管理器与房间广播

`CommentConnectionManager` 使用帖子 ID 划分房间：

```python
{
    12: {用户A的WebSocket, 用户B的WebSocket},
    35: {用户C的WebSocket},
}
```

用户查看帖子 12 时只加入房间 12。帖子 12 出现新评论时：

```python
await comment_connections.broadcast(12, message)
```

服务端复制当前连接集合，再逐个调用 `send_json()`。复制集合是为了避免广播过程中断线清理修改原集合。`asyncio.Lock` 用于保护注册、移除和读取房间集合这些并发操作。

广播也会发送给评论发布者本人。前端不在点击发送后立即插入评论，而是等待统一的 `comment.created`，因此不会出现“本地插入一次、广播又插入一次”的重复评论。

## 9. 浏览器前端流程

### 9.1 加载历史评论

页面初始化时先使用 AJAX：

```javascript
ajaxRequest({url: `/api/posts/${postId}/comments`})
    .done((comments) => comments.forEach(appendComment));
```

这一步与 WebSocket 建立并行进行。前端通过 `pendingReplies` 处理竞态：如果实时回复先到达，而对应顶级评论的历史数据还未完成渲染，就暂存该回复，等顶级评论出现后再插入。

### 9.2 创建连接

前端根据当前页面协议选择地址：

```javascript
const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
socket = new WebSocket(
    `${protocol}//${window.location.host}/api/posts/${postId}/comments/ws`,
);
```

### 9.3 监听 WebSocket 事件

浏览器原生 WebSocket 主要有四个事件：

| 事件 | 含义 | 当前处理 |
| --- | --- | --- |
| `open` | 握手完成 | 发送认证消息 |
| `message` | 收到服务端消息 | 根据 `type` 更新状态或评论区 |
| `close` | 连接关闭 | 禁用表单并尝试重连 |
| `error` | 底层连接异常 | 主动关闭，统一进入 `close` 流程 |

只有收到 `authenticated` 后，评论输入框和发送按钮才启用。浏览器本地存在 Token 不等于认证成功，最终身份仍以服务端验证结果为准。

### 9.4 发送评论

提交表单时先检查正文和连接状态：

```javascript
if (!content || socket?.readyState !== WebSocket.OPEN) return;
```

然后发送 JSON：

```javascript
socket.send(JSON.stringify({
    type: "comment.create",
    content,
    parent_id: replyTarget,
}));
```

`WebSocket.send()` 接收字符串或二进制数据，所以 JavaScript 对象需要先通过 `JSON.stringify()` 转为字符串。服务端的 `receive_json()` 会完成反序列化。

### 9.5 接收并渲染

前端先解析 JSON：

```javascript
const message = JSON.parse(event.data);
```

再根据 `message.type` 分派：

```text
authenticated    -> 启用评论表单
comment.submitted -> 解除发送按钮 loading
comment.created  -> appendComment(message.data)
error            -> 显示错误信息
```

评论正文使用 `textContent` 或文本节点写入 DOM，不使用 `innerHTML`，避免用户提交的 HTML 或脚本被浏览器执行。

## 10. 一条评论的完整通信时序

假设用户 A 和用户 B 同时打开帖子 12：

```text
用户 A 浏览器        FastAPI Router       Service/数据库       房间12        用户 B 浏览器
     |                     |                    |                 |                 |
     |-- WebSocket 握手 -->|                    |                 |                 |
     |-- authenticate ---->|-- 验证 JWT/用户 -->|                 |                 |
     |<-- authenticated ---|                    |-- 注册 A ------>|                 |
     |                     |                    |                 |                 |
     |-- comment.create -->|-- Pydantic 校验 -->|                 |                 |
     |                     |-- create_comment ->|-- INSERT/COMMIT |                 |
     |                     |<-- 已保存的评论 ----|                 |                 |
     |                     |-- broadcast ------------------------->|                 |
     |<-- comment.created -----------------------------------------|                 |
     |                     |                    |                 |-- comment.created -> B
     |-- 更新自己的页面     |                    |                 |     B 更新页面     |
```

重要顺序是：

```text
校验 -> 数据库提交 -> 广播
```

不能先广播再提交，否则提交失败时其他客户端已经看到了不存在的数据。

## 11. 断线与重连

网络切换、服务器重启、代理超时或电脑休眠都可能关闭 WebSocket。当前前端最多重连 3 次，等待时间依次为：

```text
1 秒 -> 2 秒 -> 3 秒
```

重连会创建新 WebSocket，并重新发送认证消息。页面主动关闭时设置 `closedByPage = true`，避免离开页面后仍安排重连。

当前实现没有补拉“断线期间错过的消息”。重连后如果要求严格不漏消息，可以再次调用历史评论接口，或者在协议中增加最后已知评论 ID，让后端返回缺失增量。

## 12. 安全边界

1. 不信任客户端提交的用户 ID，作者始终来自 JWT。
2. 不信任客户端提交的 `root_id`，Service 根据数据库父评论计算。
3. 父评论必须属于 URL 指定的同一帖子，禁止跨帖子回复。
4. Token 不放在 WebSocket URL 中。
5. 认证设有 10 秒超时，未认证连接不会进入广播房间。
6. Pydantic 限制正文长度，数据库字段长度保持一致。
7. 前端通过文本节点显示评论，避免 XSS。
8. 生产环境页面使用 HTTPS 时必须使用 `wss://`。

WebSocket 长连接仍应在反向代理和应用层配置连接数、消息大小、空闲超时与速率限制。当前项目尚未实现评论发送频率限制。

## 13. 单进程与多进程部署

当前连接房间保存在 Python 进程内存中。如果只运行一个 Uvicorn Worker：

```text
所有连接 -> 同一个 CommentConnectionManager -> 广播正常
```

如果运行多个 Worker：

```text
用户 A -> Worker 1 的房间12
用户 B -> Worker 2 的房间12
```

Worker 1 的内存无法直接访问 Worker 2 的连接，所以用户 A 的评论可能无法推送给用户 B。数据库保存仍然成功，只是跨进程实时广播缺失。

多进程或多实例部署需要 Redis Pub/Sub 等消息总线：

```text
Worker 1 保存评论 -> 发布到 Redis 频道 post:12
Worker 1/2/3 订阅频道 -> 各自推送给本进程中的房间12连接
```

Redis 负责跨进程传递事件，WebSocket Manager 仍负责管理各进程自己的真实连接。

## 14. 常见问题排查

### 浏览器一直显示“连接中”

- 检查 Network 面板中的 WebSocket 是否返回 `101 Switching Protocols`。
- 检查地址是否使用正确的 `ws://` 或 `wss://`。
- 检查反向代理是否转发 `Upgrade` 和 `Connection` Header。
- 检查 JavaScript 控制台是否有混合内容或连接错误。

### 连接后立即关闭

- 检查第一条消息是否为 `authenticate`。
- 检查 Access Token 是否过期。
- 检查帖子和用户是否存在。
- 查看关闭码是否为 `1008`，以及关闭前收到的 `error` 消息。

### 自己能看到评论，其他用户看不到

- 检查两个页面是否打开同一个 `post_id`。
- 检查是否使用多个 Worker；当前内存广播器不能跨进程。
- 确认其他页面的 WebSocket 仍为 `OPEN`。

### 数据库有评论但页面没有实时出现

- 确认事务提交之后执行了 `broadcast()`。
- 检查浏览器是否收到 `comment.created`。
- 检查消息 JSON 是否符合 `CommentResponse`。
- 刷新后能看到通常说明数据库正常，问题位于广播或前端消息处理。

## 15. 测试方式

项目测试使用 `TestClient.websocket_connect()` 建立测试连接：

```python
with test_client.websocket_connect("/api/posts/11/comments/ws") as websocket:
    websocket.send_json({"type": "authenticate", "token": "valid"})
    assert websocket.receive_json()["type"] == "authenticated"

    websocket.send_json(
        {"type": "comment.create", "content": "实时评论", "parent_id": 1}
    )
    message = websocket.receive_json()
    assert message["type"] == "comment.created"
```

针对评论链路可以执行：

```powershell
uv run pytest tests/test_comments.py -vv
```

该测试使用依赖覆盖和隔离数据库，不应连接开发或生产数据库。
