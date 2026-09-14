# Service 职责与调用链详解

本文说明 `app/services/` 中每个 Service **是为哪一项用户需求而存在**、从哪里被调用、
具体执行什么、为什么采用当前写法、是否提交事务、返回什么，以及失败后由谁处理。

阅读函数时先区分两层目的：

- 产品目的：用户在什么页面做了什么，例如“从头像菜单查看自己最近收藏的文章”。
- 技术目的：为实现产品目的必须提供什么保障，例如“JOIN 文章表取得标题，并按收藏时间排序”。

只写“查询收藏记录”并不足以解释需求，因为它没有说明谁在何时使用结果，也没有说明为何
按操作时间而不是文章发布时间排序。下文会把这三部分连起来。

## 1. Service 在请求链中的位置

```text
浏览器
  -> Router：处理 HTTP/WebSocket、Depends、状态码
  -> Schema：校验输入和限制输出字段
  -> Service：执行业务规则和数据访问
  -> Model：描述数据库表和关系
  -> AsyncSession：向数据库发送 SQL
```

Router 与 Service 的区别：

| Router 负责 | Service 负责 |
| --- | --- |
| URL、GET/POST/PATCH/DELETE | 查询、创建、更新、删除的具体步骤 |
| 读取 Header、Cookie、Path、Query、Body | 规范化数据、检查业务条件 |
| `CurrentUser`、`AdminUser` 权限 | 接收已经确认的 user/post 等对象 |
| 把异常转换成 400/401/403/404/409 | 抛出有业务含义的异常或返回 `None` |
| `response_model` 和成功状态码 | 管理事务并返回 ORM 对象或 Schema |

Service 不应该自己创建新的 Session。Router 通过 `Depends(get_db)` 得到 `AsyncSession`，再把同一个 Session 传入 Service，便于事务控制和测试替换。

## 2. 先理解常见数据库操作

### `select()`

```python
statement = select(User).where(User.id == user_id)
```

这里只是在 Python 中组装 SQL，还没有访问数据库。`await session...` 才会真正执行。

### `scalar()` 与 `scalars()`

```python
user = await session.scalar(select(User).where(User.id == user_id))
users = await session.scalars(select(User))
```

- `scalar()`：取第一行的第一个值，常用于“查一个对象”；没有结果返回 `None`。
- `scalars()`：得到多个第一列结果，再用 `list(...)` 转成列表。
- `execute()`：保留完整行，适合 `select(Post, activity_time)` 这种多列结果。

### `add()`

```python
session.add(user)
```

`add()` 只是让 Session 开始跟踪对象，通常还没有真正提交数据库。后续 `flush()` 或 `commit()` 才会发送 INSERT。

### `commit()`

```python
await session.commit()
```

提交当前事务。成功后其他事务才能稳定看到本次写入；失败时需要 `rollback()` 才能继续使用该 Session。

### `refresh()`

```python
await session.refresh(user)
```

重新从数据库读取对象，常用于取得数据库生成的主键、默认时间或 SQL 表达式更新后的值。它不是提交操作。

### `rollback()`

```python
await session.rollback()
```

撤销当前失败事务并恢复 Session 可用状态。它不能撤销已经成功 `commit()` 的历史事务。

### 预加载关系

异步 ORM 不适合在序列化阶段临时触发隐式查询，因此 Service 常使用：

```python
selectinload(Post.author)
joinedload(Comment.parent).joinedload(Comment.author)
```

Service 在 Session 有效时提前把响应所需关系加载好，Pydantic 后续只读取已有属性。

## 3. `auth.py`：密码、JWT 和刷新会话

文件：[app/services/auth.py](../../app/services/auth.py)

上游：`api_auth.py`、`dependencies/auth.py`、`users.py`。

### `hash_password(password)`

- 入口：注册、管理员创建用户、修改密码。
- 工作：使用 Argon2 把明文密码转换成不可逆哈希。
- 为什么使用工作线程：Argon2 是 CPU 密集计算，直接运行会阻塞 asyncio 事件循环。
- 返回：可保存到 `users.hashed_password` 的字符串。
- 不负责：写数据库、记录密码、返回 HTTP。

### `verify_password(plain_password, hashed_password)`

- 入口：登录、修改密码时验证旧密码。
- 工作：让 Argon2 从已有哈希读取盐和参数，再验证明文。
- 返回：匹配为 `True`，不匹配为 `False`。
- 不能把明文重新 hash 后比较字符串，因为 Argon2 每次使用随机盐。

### `authenticate_user(session, username, password)`

- 入口：`api_auth._authenticate()`。
- 工作：把登录标识转小写，同时匹配用户名或邮箱；查到用户后验证密码。
- 返回：成功为 `User`，用户名不存在或密码错误都返回 `None`。
- 安全原因：两类失败使用同一结果，避免接口帮助攻击者枚举账号。

```text
登录标识
  -> strip/lower
  -> WHERE lower(username)=值 OR lower(email)=值
  -> 没用户：None
  -> 有用户：verify_password
  -> 匹配返回 User，否则 None
```

### `create_access_token(user_id, expires_delta=None)`

- 入口：登录成功、Refresh 成功。
- 工作：生成包含 `sub`、`iat`、`exp` 的短期 JWT 并用服务端密钥签名。
- 返回：JWT 字符串。
- 不访问数据库，不提交事务。
- `sub` 使用稳定用户主键，不使用可能变化的昵称或邮箱。

### `verify_access_token(token)`

- 入口：`get_current_user()` 和 WebSocket 首消息认证。
- 工作：校验签名、算法、过期时间以及必需 Claims。
- 返回：`sub` 转换后的整数用户 ID。
- 失败：保留 PyJWT 异常，由 Depends 或 WebSocket Router 转成统一认证失败。

Refresh 生命周期已经从 `auth.py` 移到 `refresh_sessions.py`。这样 Access JWT 编解码不依赖
Redis，只有登录、刷新、退出和账户安全事件进入有状态会话层。

## 3.1 `refresh_sessions.py`：Redis Refresh 会话

文件：[app/services/refresh_sessions.py](../../app/services/refresh_sessions.py)

### `_digest(raw_token)`

- 入口：创建、刷新和撤销。
- 工作：把 Cookie 随机串转换为 SHA-256 摘要。
- 目的：Redis 泄露时，攻击者不能直接把 Key 中摘要作为 Refresh Cookie 使用。

### `create_refresh_session(redis, user_id)`

- 入口：登录成功。
- 工作：生成高熵 Token，Redis 原子写入会话 JSON、TTL 和用户会话索引。
- 返回：原始 Token 只交给 Router 写 HttpOnly Cookie。
- 不访问业务数据库，也不创建 SQLAlchemy 事务。

### `rotate_refresh_session(redis, raw_token)`

- 入口：`POST /api/auth/refresh`。
- 工作：检查绝对/空闲期限，用 Lua 原子删除旧 Key 并创建新 Key。
- 返回：成功为 `(user_id, new_token)`，失效或重放返回 `None`。
- 新会话继承首次登录的绝对期限，不能靠持续刷新无限续命。

### `revoke_refresh_session(redis, raw_token)`

- 入口：退出登录、刷新后发现用户已被删除。
- 工作：幂等删除当前会话 Key，并从用户索引移除摘要。

### `revoke_user_refresh_sessions(redis, user_id)`

- 入口：修改密码、管理员删除用户。
- 工作：读取用户会话索引，通过 Redis事务删除全部设备会话和索引。
- 目的：账户安全状态变化后不能再用旧 Refresh Token 恢复登录。

## 4. `users.py`：用户业务和数据访问

文件：[app/services/users.py](../../app/services/users.py)

上游：用户 Router、管理员 Router、认证 Router、页面 Router。

### `_normalize_username()` / `_normalize_email()`

- 去掉首尾空格并转小写。
- 保证注册、登录和唯一性判断使用同一规则。
- 纯 Python 函数，不访问数据库。

### `_ensure_unique_identity(session, username, email, exclude_user_id=None)`

- 入口：创建用户、管理员修改用户名或邮箱。
- 工作：大小写不敏感查询用户名或邮箱是否已存在。
- `exclude_user_id`：更新本人时排除当前行，否则原值会和自己冲突。
- 失败：抛 `UserAlreadyExistsError`，Router 转成 409。
- 数据库唯一约束仍需保留，因为“先查后写”无法独自防住并发请求。

### `create_user(session, data, is_admin=False)`

- 入口：公开注册和管理员创建用户的复用底层函数。
- 顺序：规范化 -> 主动检查冲突 -> Argon2 哈希 -> 构造 User -> add -> commit -> refresh。
- `is_admin` 只能由可信后端调用方传入，公开 Schema 没有该字段。
- 异常：数据库并发唯一冲突时 rollback，再抛业务异常。

### `list_users(session, offset, limit)`

- 入口：公开用户列表、管理员用户列表。
- 工作：按 ID 稳定排序，应用 offset/limit。
- 返回：`list[User]`。
- 只读，不提交事务。

### `get_user(session, user_id)`

- 入口：资料页、用户详情、各 Router 的 404 辅助函数。
- 使用 `session.get(User, 主键)`，主键查询比手写普通过滤更直接。
- 返回：存在为 `User`，不存在为 `None`。

### `update_user(session, user, data)`

- 入口：普通个人资料更新。
- 当前普通 Schema 只允许昵称和邮箱。
- 使用 `exclude_unset=True`：没出现在 PATCH 请求里的字段保持原值。
- 邮箱修改前规范化并检查冲突。
- 事务：commit 后 refresh；数据库冲突时 rollback。

### `delete_user(session, user)`

- 入口：用户删除 API 和管理员删除。
- Router 已经检查本人/管理员权限，Service 只删除传入对象并 commit。
- 外键 `CASCADE` 会清理用户关联的文章、评论和活动数据。

### `create_admin_managed_user()`

- 入口：后台新增用户。
- 把管理员 Schema 转为底层 `UserCreate`，再调用 `create_user()`。
- 允许可信管理员决定新用户是否为管理员。

### `update_admin_managed_user()`

- 入口：后台编辑用户。
- 可以维护用户名、昵称、邮箱和管理员角色。
- Router 在进入前负责禁止管理员撤销自己的角色。

### `set_profile_image(session, user, filename)`

- 入口：头像 Router 在图片文件保存成功后调用。
- 只把随机文件名写入 `users.image_file`，不保存完整 URL。
- 事务：commit + refresh。

### `change_password(session, user, current_password, new_password)`

- 入口：修改密码 Router。
- 先验证旧密码；失败返回 `False` 且不写数据库。
- 成功后 hash 新密码、写入、commit，返回 `True`。
- 明文密码不会进入 Model 或响应。

## 5. `posts.py`：文章查询与写入

文件：[app/services/posts.py](../../app/services/posts.py)

上游：页面 Router、文章 API、互动 API、评论 Router。

### `create_post(session, data)`

- 入口：管理员发帖 API、测试数据准备。
- 查询作者；不存在抛 `PostAuthorNotFoundError`。
- 按 `category_ids` 批量查分类；内部旧调用仍可传单个 `category_id` 并回退“其它”；只要有
  一个 ID 不存在就抛 `PostCategoryNotFoundError`。
- 通过 `author` 和 `categories` 多对多关系构造 Post，add + commit。
- 再调用 `get_post()` 重新加载作者和分类集合，避免异步序列化触发懒加载。

### `update_post(session, post, data)`

- 入口：文章编辑 API。
- `exclude_unset=True` 只更新 PATCH 实际提供的字段。
- 空 PATCH 直接返回原 Post，不产生无意义 commit。
- `category_ids` 字段单独批量查询并整体替换关系，标题和正文逐字段赋值；显式提交空数组
  会在 Schema 层返回 422。
- commit 后调用 `get_post()` 返回完整更新结果。

### `get_post(session, post_id)`

- 入口：文章详情页/API、编辑页、互动 Router、评论 Router。
- 使用 `selectinload` 一次准备作者和分类集合。
- 返回：`Post | None`。
- 只读，不提交。

### `_escape_like_keyword(keyword)`

- 把 `%`、`_`、反斜杠转义为普通字符。
- 原因：它们在 SQL LIKE 中是通配符，用户输入应默认按字面搜索。

### `list_posts(session, params)`

- 入口：首页、文章列表 API、后台文章管理。
- 可选按标题/正文关键词和分类 slug 过滤。
- 预加载作者/分类集合，按创建时间和 ID 倒序，应用分页；分类筛选使用关联关系的 EXISTS，
  一个帖子命中任一分类即可返回且不会重复。
- 返回 `list[Post]`，只读不提交。

### `search_post_titles(session, params)`

- 入口：全局搜索弹窗。
- 只匹配标题，只加载 `id/title`，限制少量结果。
- 目的：输入防抖搜索不需要反复传输文章正文和关系数据。

### `delete_post(session, post)`

- 入口：管理员删除文章 API。
- delete + commit。
- 评论、点赞、收藏和足迹通过外键级联清理。

## 6. `categories.py`：分类只读查询

文件：[app/services/categories.py](../../app/services/categories.py)

上游：页面 Router和文章 Service。

| 函数 | 入口与作用 | 返回 |
| --- | --- | --- |
| `list_categories()` | 首页类型标签、发帖分类下拉；按 `sort_order/id` 排序 | `list[Category]` |
| `get_category_by_id()` | 创建/更新文章时验证外键目标 | `Category | None` |
| `get_categories_by_ids()` | 批量验证创建/更新文章的多分类 ID，按运营排序返回 | `list[Category]` |
| `get_category_by_slug()` | URL 分类筛选和内部查询 | `Category | None` |
| `get_default_category()` | 内部旧调用未传分类时查 slug=`other` | `Category | None` |

这些函数都是只读操作，不调用 `commit()`。

## 7. `comments.py`：评论与回复关系

文件：[app/services/comments.py](../../app/services/comments.py)

上游：评论 HTTP Router 和评论 WebSocket Router。

### `list_comments(session, post_id)`

- 入口：文章详情加载历史评论。
- 查询该文章全部评论，按创建时间和 ID 正序。
- `joinedload(Comment.author)` 预加载评论作者。
- `joinedload(Comment.parent).joinedload(Comment.author)` 预加载被回复评论及其作者。
- 原因：Schema 的 `reply_to_author` 需要这些关系，不能在异步序列化时临时查询。

### `create_comment(session, post_id, user_id, data)`

- 入口：WebSocket 收到已认证的 `comment.create` 消息。
- `user_id` 来自认证用户，不接受前端指定。
- 无 `parent_id`：创建顶级评论，`parent_id/root_id` 都为空。
- 有 `parent_id`：确认目标评论属于同一文章；不存在抛 `CommentParentNotFoundError`。
- 回复顶级评论：`root_id = parent.id`。
- 回复另一条回复：`root_id = parent.root_id`。
- add 后 commit；提交成功再重新查询作者关系。
- 任意写入异常先 rollback，再继续抛给 Router。

```text
回复目标 parent
  -> parent 不存在或属于其他文章：拒绝
  -> parent 是顶级评论：root_id = parent.id
  -> parent 是回复：root_id = parent.root_id
  -> 新评论始终保留真实 parent_id
  -> 页面按 root_id 平铺到同一个二级回复区
```

Service 不执行 WebSocket `broadcast()`。Router 必须等 `create_comment()` commit 成功后再广播。

## 8. `images.py`：图片验证与磁盘保存

文件：[app/services/images.py](../../app/services/images.py)

上游：文章图片上传 Router、用户头像上传 Router。

### `_detect_extension(content)`

- 检查 PNG、JPEG、GIF、WebP 的二进制文件头（魔数）。
- 不信任用户提供的文件名和 Content-Type，因为它们可以伪造。
- 返回安全扩展名或 `None`。

### `save_post_image(upload)`

- 最多读取 `5 MB + 1 byte`；多出的 1 byte 用于判断文件是否超限。
- 验证文件签名，生成 UUID 随机文件名。
- 在线程中创建目录和写入磁盘，避免阻塞事件循环。
- 返回 `/media/post_images/...` 公开相对 URL。
- 失败抛 `InvalidImageError`，Router 转成 400。

### `save_profile_image(upload)`

- 使用相同大小和签名规则保存头像。
- 返回随机文件名，不返回完整 URL；用户 Service 把文件名写入 `image_file`。
- `User.image_path` 属性负责从文件名推导公开 URL。

图片保存与数据库更新不是一个跨系统事务。如果文件写入成功但数据库更新失败，可能产生未引用文件；生产系统可增加定期清理或对象存储事务补偿策略。

## 9. `post_activities.py`：浏览、点赞、收藏和历史

文件：[app/services/post_activities.py](../../app/services/post_activities.py)

上游：文章互动 Router。源码已经对 `scalar/add/delete/commit/refresh/JOIN` 逐段补充教学注释。

### `interaction_state(session, post, user_id)`

- COUNT 查询文章点赞数和收藏数。
- 游客没有 `user_id`，`liked/favorited` 返回 `False`。
- 登录用户只查关系主键是否存在，决定按钮高亮状态。
- 返回 `PostInteractionState` Schema。

### `record_view(session, post, user_id)`

- 使用 SQL `view_count = view_count + 1` 原子自增，避免并发覆盖。
- 游客只增加总浏览量。
- 登录用户查询 `(user_id, post_id)` 足迹：没有则 add，已有则更新 `viewed_at`。
- 浏览量与足迹同一事务 commit。
- refresh 重新读取 SQL 表达式更新后的浏览量。

### `toggle_like()` / `toggle_favorite()`

- 查询当前用户与文章的关系。
- 没有关系则 add，已有关系则 delete。
- commit 后重新调用 `interaction_state()`，返回数据库最终真实计数。
- 数据库唯一约束防止同一用户同一文章出现重复关系。

### `list_post_activity(session, user_id, kind)`

**它解决的用户需求**：头像 Popover 里有“赞过”“收藏”“我的足迹”入口。用户进入
“我的活动”页面并切换标签后，需要找回自己曾点赞、收藏或浏览的文章，而不是查看全站活动。

**为什么设计成一个函数加 `kind`**：三个标签最终都展示文章标题、文章发布时间、浏览量和
用户操作时间，只有关系表不同。复用一个函数可以让三个列表的排序、上限和响应结构始终一致。
Router 使用 `Literal["likes", "favorites", "views"]` 提前限制 kind，所以映射取值是可信的。

**完整执行逻辑**：

1. `likes` 选择 `PostLike`，`favorites` 选择 `PostFavorite`，`views` 选择 `PostView`。
2. 点赞/收藏的动作时间是 `created_at`；足迹重复浏览会更新，因此使用 `viewed_at`。
3. 用 `model.user_id == user_id` 只查询当前登录用户，不能让客户端指定他人 ID。
4. 行为表只有 `post_id` 和时间等关系字段，所以 JOIN `posts` 取得列表要显示的文章信息。
5. 先按活动时间倒序，再按行为记录 ID 倒序，在时间相同时仍获得稳定顺序。
6. 最多返回 100 条，避免个人历史无限增长后一次请求加载全部数据。
7. 把 `(Post, activity_at)` 组合成相同的 `UserActivityItem`，方便一个前端列表组件复用。

**为什么不按文章 `created_at` 排序**：假设一篇文章去年发布，用户今天才收藏。用户期待它
出现在“收藏”顶部；按文章发布时间会把它埋在列表末尾，因此必须按用户最近操作时间排序。

**它不负责什么**：不创建点赞、收藏或足迹，不判断登录状态，也不提交事务。记录动作分别由
`toggle_like()`、`toggle_favorite()` 和 `record_view()` 完成，认证由 Router 的 `CurrentUser` 完成。

### `list_comment_activity(session, user_id)`

- JOIN comments 与 posts，因为评论表只保存文章 ID，不重复保存标题。
- 按评论时间倒序，返回评论内容和文章上下文。

## 10. Service 异常如何到达用户

```text
Service 抛业务异常
  -> Router catch
  -> raise HTTPException
  -> 全局 exception handler
  -> 统一 ApiFailure JSON
```

示例：

| Service 异常/结果 | Router 解释 |
| --- | --- |
| `UserAlreadyExistsError` | 409 用户名或邮箱冲突 |
| `PostCategoryNotFoundError` | 404 一个或多个分类不存在 |
| `CommentParentNotFoundError` | WebSocket error 消息 |
| `InvalidImageError` | 400 图片无效或超限 |
| `authenticate_user() -> None` | 401 登录失败 |
| `get_post() -> None` | Router 辅助函数转换成 404 |

不要在 Service 中直接返回 HTML、打开登录模态框或操作 WebSocket；这些属于不同边界。

## 11. 如何顺着代码阅读一个 Service

以“点赞”为例：

```text
post.html 的 data-like-post 按钮
  -> post-activities.js click
  -> POST /api/posts/{id}/like
  -> api_activities.toggle_like Router
  -> CurrentUser Depends
  -> _post_or_404
  -> post_activities.toggle_like Service
  -> PostLike Model
  -> commit
  -> interaction_state
  -> PostInteractionState Schema
  -> 前端更新按钮和计数
```

搜索方法：

```powershell
rg -n "toggle_like" app tests
rg -n "create_comment" app tests
rg -n "change_password" app tests
```

建议每次按以下顺序阅读：

1. 看函数 docstring，确认输入、输出和副作用。
2. 看上游 Router 是否已经完成认证和 404。
3. 看查询条件，确认操作的是哪一个用户/文章。
4. 找 `add/delete/属性赋值`，确认准备修改什么。
5. 找 `commit/rollback/refresh`，确认事务边界。
6. 看返回 ORM、Schema、布尔值还是 `None`。
7. 看 Router 如何把异常或返回值转换成 HTTP/WebSocket 响应。
8. 看测试覆盖了哪些成功和失败分支。

## 12. 事务记忆方式

```text
查询：select -> scalar/scalars/execute -> 返回，不需要 commit

新增：构造 Model -> add -> commit -> 必要时 refresh

更新：查询对象 -> 修改属性 -> commit -> 必要时 refresh

删除：查询对象 -> delete -> commit

失败：捕获已知写入异常 -> rollback -> 抛业务异常
```

`commit()` 不是“保存某一个对象”，而是提交当前 Session 事务中所有尚未提交的变化。因此 Service 应保持单个业务动作的事务边界清晰，不要在不相关步骤之间随意提交。
