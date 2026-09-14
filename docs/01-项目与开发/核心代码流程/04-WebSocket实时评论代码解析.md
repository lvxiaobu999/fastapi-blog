# WebSocket 实时评论代码解析

## 1. 为什么评论同时使用 HTTP 和 WebSocket

HTTP 适合“请求一次，返回一次”：

```text
浏览器 GET 历史评论 -> 服务端返回已有评论 -> 请求结束
```

WebSocket 建立后保持双向连接：

```text
浏览器可以随时发评论
服务端也可以主动把新评论推给房间内其他浏览器
```

WebSocket 只能收到连接建立之后的增量消息，不能替代历史查询。因此页面并行启动：

```text
GET /api/posts/{post_id}/comments       -> 历史评论
WS  /api/posts/{post_id}/comments/ws    -> 新评论和实时广播
```

## 2. 相关文件

| 文件 | 职责 |
|---|---|
| [routers/comments.py](../../../app/routers/comments.py) | HTTP 历史端点、WebSocket 协议和认证流程 |
| [websockets/comments.py](../../../app/websockets/comments.py) | 按 post_id 管理连接房间和广播 |
| [services/comments.py](../../../app/services/comments.py) | 评论查询、回复关系、数据库事务 |
| [schemas/comment.py](../../../app/schemas/comment.py) | 认证消息、创建消息和公开响应契约 |
| [models/comment.py](../../../app/models/comment.py) | comments 表、parent/root 外键关系 |
| [static/js/comments.js](../../../app/static/js/comments.js) | 浏览器历史加载、连接、发送、重连和渲染 |

## 3. WebSocket 不是“没有协议”

WebSocket 只提供双向传输通道，不知道什么是登录或评论。本项目在 JSON 上定义应用层协议：

### 浏览器发给服务端

```json
{"type":"authenticate","token":"<access-token>"}
```

```json
{"type":"comment.create","content":"评论正文","parent_id":12}
```

### 服务端发给浏览器

| type | 接收者 | 含义 |
|---|---|---|
| `authenticated` | 当前连接 | 认证成功，可以发表评论 |
| `comment.submitted` | 提交者 | 评论已经提交数据库，可以解除按钮 loading |
| `comment.created` | 房间所有连接 | 新评论公开数据，用于渲染 |
| `error` | 当前连接 | 参数、身份、频率或业务错误 |

为什么同时需要 submitted 和 created：

- submitted 是给提交者的“写库成功确认”。
- created 是给所有在线用户的“渲染事件”，提交者本人也会收到。
- 前端只在 created 时插入 DOM，避免本地插入一次、广播又插入一次。

## 4. Schema 怎样限制消息类型

认证消息：

```python
class WebSocketAuthMessage(BaseModel):
    type: Literal["authenticate"]
    token: str = Field(min_length=1)
```

`Literal["authenticate"]` 表示 type 只能是这个固定字符串。传 `comment.create` 会校验失败。

创建消息：

```python
class CommentCreateMessage(CommentCreate):
    type: Literal["comment.create"]
```

它继承 `CommentCreate` 的字段：

```python
content: str = Field(min_length=1, max_length=1000)
parent_id: int | None = Field(default=None, gt=0)
```

| 限制 | 含义 |
|---|---|
| `min_length=1` | 至少一个字符 |
| `max_length=1000` | 最多 1000 字符，与数据库 String(1000) 对齐 |
| `default=None` | 不回复别人时是顶级评论 |
| `gt=0` | parent_id 必须大于 0 |

Validator：

```python
normalized = value.strip()
if not normalized:
    raise ValueError("评论内容不能为空")
return normalized
```

`"   "` 原长度大于零，但去掉空格后为空，所以仍会拒绝。返回 normalized 还会让数据库保存去掉
首尾空白后的正文。

## 5. WebSocket 端点的参数是怎样注入的

```python
@router.websocket("/ws")
async def comment_websocket(
    websocket: WebSocket,
    post_id: int,
    session: DbSession,
) -> None:
```

完整路径由 Router prefix 和 `/ws` 组合：

```text
/api/posts/{post_id}/comments/ws
```

| 参数 | 来源 |
|---|---|
| `websocket` | FastAPI/Starlette 创建的连接对象 |
| `post_id` | URL 路径参数 |
| `session` | `Depends(get_db)` 注入的 AsyncSession |

post_id 来自 URL，不接受消息体更改。因此连接一旦进入文章 10 的房间，客户端不能通过 JSON 把
评论改发到文章 11。

## 6. `websocket.accept()` 做了什么

HTTP 客户端先发送 Upgrade 握手。服务端调用：

```python
await websocket.accept()
```

表示接受升级，连接进入可收发消息状态。没有 accept 就直接 `receive_json()` 或 `send_json()` 会
违反 WebSocket 状态机。

注意：accept 只代表网络握手成功，不代表用户认证成功。应用层认证发生在第一条消息。

## 7. 服务端 WebSocket 完整调用链

```text
1. accept 网络连接
2. 查询 Post，确认文章存在且已发布
3. 最多等待 10 秒接收第一条认证消息
4. Pydantic 校验 type/token
5. verify_access_token() 验证 JWT
6. 数据库查询 User 是否仍存在
7. register(post_id, websocket) 加入房间
8. 发送 authenticated
9. while True 等待 comment.create
10. 滑动窗口限流
11. 再次验证 Token/User/Post
12. Pydantic 校验评论
13. Service 提交数据库
14. 给提交者发送 comment.submitted
15. 给房间广播 comment.created
16. 断线或错误后 finally 移出房间
```

## 8. 为什么第一条消息才发送 Token

浏览器原生 `WebSocket` API 不能像 AJAX 一样自由设置：

```http
Authorization: Bearer ...
```

也不建议把 Token 放到 URL 查询参数，因为 URL 可能进入代理访问日志和浏览器记录。因此浏览器
连接成功后立即发送：

```javascript
socket.send(JSON.stringify({
    type: "authenticate",
    token: getToken(),
}));
```

`JSON.stringify()` 把 JavaScript 对象转换成 WebSocket 可以发送的字符串。

## 9. 认证超时怎样实现

```python
async with asyncio.timeout(AUTH_TIMEOUT_SECONDS):
    raw_message = await websocket.receive_json()
```

`AUTH_TIMEOUT_SECONDS = 10`。

执行含义：

```text
10 秒内收到 JSON -> 继续
10 秒内没有收到   -> 抛 TimeoutError
```

如果不限制，攻击者可以建立大量连接后一直不认证，占用服务器连接和内存。

## 10. `receive_json()` 和 Pydantic 的边界

```python
raw_message = await websocket.receive_json()
auth_message = WebSocketAuthMessage.model_validate(raw_message)
```

两步职责不同：

- `receive_json()`：读取一帧文本并执行 JSON 解析，得到 Python dict/list。
- `model_validate()`：检查 dict 是否符合业务字段、类型和 Literal。

合法 JSON 不代表合法认证消息。例如：

```json
{"hello":"world"}
```

JSON 解析成功，但 Pydantic 会因为缺少 type/token 抛 ValidationError。

## 11. `_send_websocket_error()` 和关闭码 1008

```python
await websocket.send_json({"type": "error", "message": message})
if close:
    await websocket.close(code=1008)
```

先发送可读业务错误，让前端能提示用户；不可恢复错误再关闭连接。

1008 是 Policy Violation，表示连接违反应用策略，例如身份失效、文章下架或持续超限。它不同于
正常关闭 1000。

## 12. 为什么每条评论都重新验证身份和文章

WebSocket 可能保持数分钟甚至更久。连接建立时有效，不代表发送评论时仍有效：

- Access Token 可能刚过期。
- 用户可能已被删除。
- 文章可能刚下架。

因此 `_ensure_websocket_context()` 每条消息执行：

```text
重新 verify_access_token(access_token)
  -> session.get(User, user.id, populate_existing=True)
  -> session.get(Post, post_id, populate_existing=True)
```

`populate_existing=True` 表示即使 Session 身份映射中已经缓存过对象，也重新用数据库当前值填充，
避免一直使用连接建立时的旧状态。

## 13. WebSocket 滑动窗口限流

常量：

```python
COMMENT_RATE_LIMIT_WINDOW_SECONDS = 10
COMMENT_RATE_LIMIT_MAX_MESSAGES = 5
```

含义：同一条连接在任意连续 10 秒内最多提交 5 条消息。

Nginx `limit_req` 只能限制 WebSocket 握手 HTTP 请求。握手成功后的消息都在同一连接内，不会再次
经过 HTTP 限流，所以应用内部仍要限制。

## 14. deque 是什么

```python
message_times: deque[float] = deque()
```

deque 是双端队列，可以高效从左侧删除最旧时间、从右侧追加最新时间。

假设队列：

```text
[1.0, 2.0, 4.0, 7.0, 8.0]
```

左侧是最早消息，右侧是最新消息。

限流步骤：

```python
now = monotonic()
while message_times and now - message_times[0] >= 10:
    message_times.popleft()
```

- `message_times` 用作布尔值：空队列是 False，避免读取 `[0]` 报 IndexError。
- `message_times[0]` 是最早时间。
- `popleft()` 删除最早记录。
- while 会一直删除所有已经离开时间窗口的记录。

然后：

```python
if len(message_times) >= 5:
    发送错误并关闭
message_times.append(now)
```

## 15. 为什么使用 `monotonic()`

`time.monotonic()` 返回只保证不断向前增加的时钟，适合计算间隔。

系统时间可能因为 NTP 校准、管理员修改时间或时区变化向前/向后跳。限流只关心“过了几秒”，
不关心真实日期，所以不用 `datetime.now()`。

## 16. 每条连接为什么有自己的 message_times

```python
user, access_token = authenticated
message_times: deque[float] = deque()
```

变量在 `comment_websocket()` 函数内部创建，因此每个连接协程有独立队列：

```text
连接 A -> deque A
连接 B -> deque B
```

断开后函数结束，队列没有其他引用，会由 Python 回收，不需要数据库表或清理任务。

它限制单连接，不是用户跨多个连接或多个实例的全局限流。生产更强限制需要网关/Redis/WAF。

## 17. Comment Model 的 parent_id 和 root_id

评论结构：

```text
顶级评论 A：parent_id=None, root_id=None
回复 B 回复 A：parent_id=A, root_id=A
回复 C 回复 B：parent_id=B, root_id=A
```

两个字段用途不同：

| 字段 | 回答的问题 |
|---|---|
| `parent_id` | 这条回复直接回复谁？用于显示 @昵称 |
| `root_id` | 这条回复属于哪个顶级讨论串？用于页面分组 |

前端只允许提交 parent_id。root_id 必须由服务端根据可信数据库关系计算，不能让客户端伪造。

## 18. 自引用外键和 relationship

```python
parent_id = ForeignKey("comments.id", ondelete="CASCADE")
root_id = ForeignKey("comments.id", ondelete="CASCADE")
```

comments 表的字段指回 comments 表自己的 id，叫自引用外键。

```python
parent = relationship(
    foreign_keys=[parent_id],
    remote_side=[id],
)
```

因为 parent_id 和 root_id 都指向 comments.id，SQLAlchemy 无法自动猜哪一个属于 `parent` 关系：

- `foreign_keys=[parent_id]` 明确使用 parent_id。
- `remote_side=[id]` 明确 id 是关系的父级一侧。

## 19. `create_comment()` 逐步解析

输入变量：

| 变量 | 来源 | 是否可信 |
|---|---|---|
| `session` | Depends 注入 | 可信基础设施 |
| `post_id` | WebSocket URL | 仍需检查文章存在 |
| `user_id` | 已验证 JWT 的 User | 服务端身份结果 |
| `data.content` | 客户端消息 | 已经 Pydantic 校验 |
| `data.parent_id` | 客户端消息 | 需要查数据库确认 |

没有 parent_id：

```python
parent = None
root_id = None
```

有 parent_id 时查询：

```python
select(Comment).where(
    Comment.id == data.parent_id,
    Comment.post_id == post_id,
)
```

同时检查 id 和 post_id，禁止拿其他文章的评论 ID 作为回复目标。

计算 root：

```python
root_id = parent.root_id or parent.id
```

条件含义：

- 回复顶级评论：`parent.root_id` 是 None，使用 `parent.id`。
- 回复已有回复：沿用它的 `parent.root_id`。

创建 ORM：

```python
comment = Comment(
    post_id=post_id,
    user_id=user_id,
    content=data.content,
    parent_id=parent.id if parent else None,
    root_id=root_id,
)
```

用户不能提交 user_id/root_id，身份和讨论归属都由服务端决定。

## 20. 为什么必须提交数据库后再广播

```text
session.add(comment)
  -> await session.commit()
  -> 查询并预加载作者关系
  -> Router 发送 submitted
  -> Router broadcast created
```

如果先广播再 commit：

```text
其他浏览器看到评论
  -> 数据库 commit 失败
  -> 页面刷新后评论消失
```

当前顺序保证广播的评论已经持久化。

写入异常时执行：

```python
await session.rollback()
raise
```

rollback 清理失败事务状态；`raise` 保留原异常和 traceback，交给上层处理。

## 21. joinedload 为什么需要

评论响应需要：

```text
comment.author
comment.parent.author
```

`joinedload()` 提前加载关系，避免 Pydantic 序列化时才临时查询数据库。异步 SQLAlchemy 中，在
不合适的上下文触发隐式懒加载可能报错，也会形成 N+1 查询。

```python
joinedload(Comment.parent).joinedload(Comment.author)
```

含义是先加载 parent，再加载 parent 的 author。

## 22. CommentConnectionManager 的房间结构

```python
self._rooms: dict[int, set[WebSocket]] = defaultdict(set)
```

实际形状：

```text
{
    10: {socket_a, socket_b},
    11: {socket_c},
}
```

- dict key 是 post_id。
- value 是正在查看该帖子的 WebSocket 集合。
- set 自动去重，同一连接不会重复加入。
- `defaultdict(set)` 在缺少 post_id 时自动创建空 set。

普通 dict 需要：

```python
if post_id not in rooms:
    rooms[post_id] = set()
rooms[post_id].add(websocket)
```

defaultdict 可以直接 `rooms[post_id].add(...)`。

## 23. 为什么房间操作需要 asyncio.Lock

多个协程可能交错执行：

```text
连接 A register
连接 B disconnect
连接 C broadcast
```

`asyncio.Lock()` 让修改 `_rooms` 的小段代码互斥：

```python
async with self._lock:
    self._rooms[post_id].add(websocket)
```

锁只保护内存集合，不包住网络发送。网络 I/O 可能很慢，如果持锁发送，其他用户无法及时加入或
退出房间。

## 24. register 和 disconnect 逐步解释

注册：

```python
self._rooms[post_id].add(websocket)
```

把连接加入文章房间。

断开：

```python
room = self._rooms.get(post_id)
if room is None:
    return
room.discard(websocket)
if not room:
    self._rooms.pop(post_id, None)
```

| API | 含义 |
|---|---|
| `.get(post_id)` | 不存在返回 None，不抛 KeyError |
| `.discard(socket)` | 删除元素；元素不存在也不报错 |
| `if not room` | 空 set 为 False |
| `.pop(post_id, None)` | 删除空房间；Key 不存在返回 None |

`discard` 和带默认值的 `pop` 让重复断开保持幂等。

## 25. 广播为什么先复制 tuple 快照

```python
async with self._lock:
    sockets = tuple(self._rooms.get(post_id, ()))
```

步骤：

1. 持锁读取当前 set。
2. `tuple(...)` 复制一份不可变快照。
3. 释放锁。
4. 在锁外执行网络发送。

如果直接遍历原 set，同时另一个协程删除连接，可能出现“集合在迭代期间改变”错误。

默认值 `()` 是空 tuple；房间不存在时广播自然没有目标。

## 26. 嵌套 `send()` 函数

```python
async def send(socket: WebSocket) -> WebSocket | None:
```

返回约定：

```text
发送成功 -> None
发送失败/超时 -> 返回这个 socket
```

超时：

```python
async with asyncio.timeout(2):
    await socket.send_json(message)
```

单个慢客户端最多等待 2 秒，不能无限拖住整个房间。

捕获：

```python
except (RuntimeError, TimeoutError):
    return socket
```

- TimeoutError：超过两秒。
- RuntimeError：连接状态已经不能发送等运行时问题。

## 27. `asyncio.gather()` 为什么比逐个 await 更好

逐个发送：

```text
等 A 发送完 -> 等 B -> 等 C
```

并发发送：

```python
await asyncio.gather(
    *(send(socket) for socket in sockets)
)
```

解释：

- 生成器为每个 socket 产生一个 send 协程。
- `*` 把协程序列展开成 gather 的多个位置参数。
- `gather` 并发等待全部结果，并按输入顺序返回结果列表。

之后列表推导收集非 None：

```python
stale = [socket for socket in results if socket is not None]
```

`stale` 表示失效连接，再逐个调用 disconnect 清理。

## 28. 为什么 `finally` 一定要 disconnect

```python
registered = False
try:
    ...
    await register(...)
    registered = True
    ...
finally:
    if registered:
        await disconnect(...)
```

认证前连接不在房间，不能盲目清理。成功 register 后无论正常断开、策略关闭、JSON 错误还是代码
异常，finally 都执行，防止 `_rooms` 永久保留失效 socket。

## 29. 前端关键状态变量

`comments.js` 中：

| 变量 | 含义 |
|---|---|
| `postId` | 从 HTML `data-post-id` 读取的当前文章 ID |
| `total` | 已渲染评论数量 |
| `replyTarget` | 当前直接回复的评论 ID，null 表示顶级评论 |
| `rootBodies` | 顶级评论 ID到回复容器 DOM 的 Map |
| `pendingReplies` | 顶级评论尚未渲染时暂存的回复 |
| `socket` | 当前浏览器 WebSocket 对象 |
| `reconnectAttempts` | 连续意外断线重试次数 |
| `reconnectTimer` | setTimeout 返回的任务 ID |
| `closedByPage` | 是否因刷新/离开页面主动关闭 |
| `connectionReady` | 服务端是否已发送 authenticated |
| `submitting` | 是否有评论正在等待提交确认 |

## 30. `data-*` 和 dataset

HTML：

```html
<section data-comments data-post-id="12">
```

JavaScript：

```javascript
const root = document.querySelector("[data-comments]");
const postId = Number(root.dataset.postId);
```

- `dataset.postId` 对应 `data-post-id`。
- dataset 返回字符串，所以用 `Number()` 转为数字。
- 找不到根节点说明不是帖子详情页，脚本直接 return。

## 31. 历史 HTTP 和实时消息为什么会重复

页面同时启动 HTTP 和 WebSocket：

```text
HTTP 开始查询历史
  -> 用户此时发布评论
  -> WebSocket 先收到 comment.created
  -> HTTP 稍后返回，里面也包含刚提交的评论
```

前端按 ID 去重：

```javascript
if ([...list.children].some(
    (item) => item.dataset.commentId === String(comment.id)
)) return;
```

- `list.children` 是 HTMLCollection。
- `[...list.children]` 展开成数组，才能使用 `some()`。
- `some()` 只要有一个元素 ID 相同就返回 true。

## 32. 为什么使用 createElement/textContent

```javascript
const content = document.createElement("p");
content.textContent = comment.content;
```

不使用：

```javascript
content.innerHTML = comment.content;
```

`textContent` 会把 `<script>` 当普通文字展示，不当 HTML 执行，降低存储型 XSS 风险。

回复 @昵称使用：

```javascript
content.append(
    mention,
    document.createTextNode(comment.content),
);
```

正文仍是文本节点，不是 HTML。

## 33. rootBodies 和 pendingReplies 怎样解决乱序

`rootBodies`：

```text
顶级评论 ID -> 它下面的 replies DOM
```

如果回复到达时顶级评论已经渲染，直接追加。

如果 WebSocket 回复先于 HTTP 顶级评论到达：

```javascript
const pending = pendingReplies.get(rootId) ?? [];
pending.push(article);
pendingReplies.set(rootId, pending);
```

`?? []` 表示左边是 null/undefined 时使用新数组。

顶级评论稍后创建时：

```javascript
(pendingReplies.get(comment.id) ?? [])
    .forEach((item) => replies.append(item));
pendingReplies.delete(comment.id);
```

先搬运暂存回复，再删除缓存。

## 34. 前端连接生命周期

`connect()`：

```text
没有 Access Token -> 显示登录后可评论，不连接
  -> 根据页面协议选择 ws 或 wss
  -> new WebSocket(url)
  -> 监听 open/message/close/error
```

协议选择：

```javascript
const protocol = window.location.protocol === "https:"
    ? "wss:"
    : "ws:";
```

HTTPS 页面必须使用加密的 wss，否则浏览器会阻止混合内容。

## 35. open 不等于 authenticated

```text
open -> TCP/WebSocket 通道建立
authenticated -> 服务端已经验证 JWT 并加入房间
```

所以前端用 `connectionReady`，不能只判断 `socket.readyState === OPEN` 就允许输入。

服务端收到 authenticate 后先 register，再发送 authenticated。浏览器收到后才启用表单。

## 36. 提交按钮为什么有 submitting

发送前：

```javascript
if (!content || submitting || socket?.readyState !== WebSocket.OPEN) return;
```

| 条件 | 原因 |
|---|---|
| `!content` | 空正文不发送 |
| `submitting` | 上一条还没确认，阻止重复点击 |
| 非 OPEN | 连接不能发送 |

发送后 `submitting=true` 并禁用输入。收到：

- `comment.submitted`：清空正文，解除 loading。
- `error`：保留正文，解除 loading，方便修改后重试。
- `close`：保留正文，等待连接恢复。

后端仍会做最终 Schema 和限流检查，前端限制只改善体验，不是安全边界。

## 37. 重连逻辑

意外断线后最多尝试三次：

```text
第 1 次等待 1 秒
第 2 次等待 2 秒
第 3 次等待 3 秒
```

```javascript
reconnectTimer = window.setTimeout(
    connect,
    1000 * reconnectAttempts,
);
```

认证成功后 `reconnectAttempts=0`。

页面离开：

```javascript
closedByPage = true;
window.clearTimeout(reconnectTimer);
socket?.close();
```

`?.` 是可选链：socket 为 null 时不调用 close，也不报错。主动离开不应该触发自动重连。

## 38. 常见失败分支

| 场景 | 服务端行为 | 前端行为 |
|---|---|---|
| 文章不存在/下架 | error + 1008 close | 显示连接断开 |
| 10 秒未认证 | error + close | 根据 close 规则有限重连 |
| Token 无效/过期 | error + close | `requireLogin()` |
| 用户已删除 | error + close | 要求重新登录 |
| 评论格式错误 | error，不关闭 | 保留连接，可修改重试 |
| parent 不存在/跨文章 | error，不关闭 | 保留连接，可取消回复 |
| 10 秒超过 5 条 | error + close | 显示频率错误 |
| 数据库提交失败 | rollback，异常上抛 | 连接可能关闭，正文保留 |
| 向单个客户端广播超时 | 从内存房间移除 stale socket | 不保证收到 close/error，当前代码也不会主动 `close()` |

最后一行要特别区分“移出广播房间”和“关闭 WebSocket”。当前 `broadcast()` 只调用
`disconnect()` 修改 `_rooms`，没有调用 `socket.close()`；因此这个客户端不会再收到本进程的
房间广播，但它自己的端点接收循环可能暂时仍在运行。这是当前实现边界，不应把它理解成服务端
已经完整关闭了网络连接。

## 39. 单进程房间的限制

`comment_connections` 是当前 Python 进程中的普通内存对象：

```python
comment_connections = CommentConnectionManager()
```

如果运行两个 worker：

```text
用户 A 连接 worker 1
用户 B 连接 worker 2
worker 1 的 _rooms 看不到用户 B
```

数据库评论仍会保存，但跨 worker 不能实时广播。扩容前需要 Redis Pub/Sub、Streams 或消息系统
在实例之间传递 comment.created 事件。

## 40. 调试顺序

建议断点：

1. 浏览器 `connect()`：确认 URL 是 ws/wss，postId 正确。
2. `handleSocketOpen()`：确认第一条消息 type 是 authenticate，不输出完整 Token。
3. `_authenticate_websocket()`：确认 JWT 和 User 查询。
4. `register()`：观察 `_rooms` 中 post_id 和连接数量。
5. `_create_comment_from_message()`：观察限流、上下文复核和 Schema。
6. `create_comment()`：观察 parent/root 计算和 commit。
7. `broadcast()`：观察 sockets 快照、gather 结果和 stale。
8. 前端 `handleSocketMessage()`：确认 submitted 与 created 分工。

测试入口：[tests/test_comments.py](../../../tests/test_comments.py)。
