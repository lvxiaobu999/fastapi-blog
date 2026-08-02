# Router 入口与请求调用链详解

本文回答三个问题：一个 Router 从哪里被触发、进入后做什么、完成后把工作交给谁。

## 1. Router 到底是什么

Router 是 FastAPI 的 HTTP/WebSocket 入口层。浏览器请求到达后，FastAPI 根据“请求方法 + 路径”找到对应函数。

```text
浏览器点击或 JavaScript 请求
  -> FastAPI app
  -> APIRouter 匹配方法和路径
  -> Depends 解析数据库、当前用户或管理员
  -> Schema 校验输入
  -> Router 调用 Service
  -> Service 访问 ORM / 数据库
  -> Router 按 response_model 返回响应
```

例如：

```python
@router.post("/{post_id}/like")
async def toggle_like(...):
    ...
```

- `@router.post`：只接收 HTTP POST，不会响应 GET。
- `/{post_id}`：从 URL 取文章 ID，FastAPI 会把它转换为 `int`。
- 函数参数里的 `Depends`：函数执行前先完成数据库或认证依赖。
- 函数体：只做 HTTP 边界工作和 Service 调用，不把复杂 SQL 堆在 Router。

## 2. Router 在哪里注册

[app/routers/__init__.py](../app/routers/__init__.py) 把各模块的 `router` 改成容易识别的导出名称；[app/main.py](../app/main.py) 使用 `app.include_router(...)` 注册。

```text
pages_router
api_activities_router
api_admin_router
api_auth_router
api_posts_router
comments_router
api_users_router
```

只定义 `APIRouter` 而没有 `include_router`，请求永远不会进入它。

## 3. 两类入口

### 页面 Router

`pages.py` 返回完整 HTML。入口通常是：

- 地址栏输入 URL。
- 点击普通 `<a href="...">` 链接。
- JavaScript 使用 `window.location.assign(...)` 跳转。

### API Router

`api_*.py` 和 `comments.py` 返回 JSON 或建立 WebSocket。入口通常是：

- `api.js` 的 `ajaxRequest(...)`。
- jQuery `$.ajax(...)`。
- `uploadFile(...)` 文件上传。
- 浏览器 `WebSocket(...)`。
- Swagger、测试或其他客户端直接发送请求。

页面是否显示按钮不等于后端权限。攻击者可以绕过页面直接请求 API，所以受保护 API 必须使用 `CurrentUser` 或 `AdminUser`。

## 4. `pages.py`：HTML 页面入口

文件：[app/routers/pages.py](../app/routers/pages.py)

这个 Router 设置 `include_in_schema=False`，因此不出现在 Swagger；它只渲染模板，不接收资料、文章、评论等写操作。

| 方法与路径 | 从哪里触发 | 主要工作 |
| --- | --- | --- |
| `GET /` | 点击站点 Logo、导航“文章”、直接访问首页 | 与 `/posts` 共用 `home()`；查询文章和分类，渲染 `home.html` |
| `GET /posts` | 首页类型标签、搜索后跳转、浏览全部文章 | 读取 `keyword/category/offset/limit`，调用文章与分类 Service，渲染筛选后的列表 |
| `GET /login` | 直接访问兼容登录页 | 渲染 `login.html`；主导航通常使用登录模态框 |
| `GET /register` | 直接访问兼容注册页 | 渲染 `register.html`；注册写入实际走 `POST /api/users` |
| `GET /profile/{user_id}` | 头像菜单“编辑个人信息” | 查用户；不存在返回 404；渲染资料与头像即时上传界面 |
| `GET /me/activities` | 头像菜单“评论/赞过/收藏/我的足迹” | 只渲染活动页外壳；`post-activities.js` 再按 `tab` 请求受保护 API |
| `GET /posts/new` | 管理员头像菜单“发布新帖子” | 查询分类并渲染后台编辑器；真正发布走 `POST /api/posts` |
| `GET /posts/{id}/edit` | 后台文章管理“编辑” | 查文章和分类，渲染带原值的编辑器；保存走 `PATCH /api/posts/{id}` |
| `GET /posts/{id}` | 文章列表、搜索结果、活动历史中的文章链接 | 查文章并渲染详情；页面脚本随后记录浏览并加载评论 |
| `GET /admin` | 头像菜单“进入我的后台” | 渲染后台概览外壳；`admin.js` 再验证当前用户是否为管理员 |
| `GET /admin/users` | 后台侧栏“用户管理” | 渲染用户管理工作区；数据来自 `/api/admin/users` |
| `GET /admin/posts` | 后台侧栏“帖子管理” | 渲染帖子管理工作区；数据来自 `/api/posts` |

示例调用链：

```text
点击文章标题
  -> GET /posts/12
  -> pages.post_detail()
  -> posts.get_post(session, 12)
  -> 找不到：HTML 404
  -> 找到：post.html
  -> 浏览器加载 post-activities.js 和 comments.js
```

## 5. `api_auth.py`：登录和会话

文件：[app/routers/api_auth.py](../app/routers/api_auth.py)，统一前缀 `/api/auth`。

| 方法与路径 | 从哪里触发 | 主要工作 |
| --- | --- | --- |
| `POST /api/auth/token` | `auth.js` 登录表单 | 校验用户名/邮箱和密码；签发 Access Token；创建 Refresh Session；写 HttpOnly Cookie |
| `POST /api/auth/oauth2-token` | Swagger 的 Authorize/OAuth2 Password Flow | 使用 OAuth2 要求的顶层 Token 响应，便于 Swagger 自动认证 |
| `POST /api/auth/refresh` | `api.js` 发现受保护请求返回 401 | 从 HttpOnly Cookie 取 Refresh Token；轮换会话；返回新 Access Token |
| `POST /api/auth/logout` | 头像菜单“退出登录” | 撤销 Refresh Session，删除 Refresh Cookie；前端再清理 localStorage Access Token |
| `GET /api/auth/me` | 每次有缓存 Token 的页面启动、后台启动 | `CurrentUser` 验证 JWT 并查数据库，返回最新昵称、头像和 `is_admin` |
| `POST /api/auth/password` | 修改密码模态框 | 验证旧密码，校验新密码，写入新哈希；绝不保存明文密码 |

登录链：

```text
提交登录模态框
  -> auth.js ajaxRequest(/api/auth/token)
  -> api_auth.login()
  -> _authenticate()
  -> auth Service 校验密码
  -> 创建 Refresh Session + Access Token
  -> Refresh Token 写 HttpOnly Cookie
  -> Access Token 返回给 JS 存入 localStorage
```

`CurrentUser` 位于 `app/dependencies/auth.py`。它不是一个普通类型，而是“先执行认证依赖，再把 User 对象注入函数”的声明。

## 6. `api_users.py`：注册和个人资料

文件：[app/routers/api_users.py](../app/routers/api_users.py)，统一前缀 `/api/users`。

| 方法与路径 | 从哪里触发 | 主要工作 |
| --- | --- | --- |
| `POST /api/users` | 注册模态框或注册页 | Schema 校验用户名、邮箱、密码；Service 规范化身份、哈希密码并创建用户；冲突返回 409 |
| `GET /api/users` | API/测试调用 | 分页查询用户；当前是公开接口，不能返回密码哈希 |
| `GET /api/users/{id}` | 公开资料/API 调用 | 查询单个用户，不存在返回 404 |
| `PATCH /api/users/{id}` | 个人资料页“保存修改” | 要求登录；仅本人或管理员可修改；普通资料只接受昵称和邮箱 |
| `DELETE /api/users/{id}` | API 调用 | 要求登录；仅本人或管理员可删除；Service 提交删除事务 |
| `POST /api/users/me/avatar` | 资料页选择头像后立即触发 | 从 `CurrentUser` 确定目标用户，校验图片签名和大小，保存随机文件名并更新 `image_file` |

头像入口链：

```text
选择头像文件
  -> forms.js change 事件
  -> uploadFile(/api/users/me/avatar)
  -> CurrentUser 验证
  -> images.save_profile_image()
  -> users.set_profile_image()
  -> 返回 image_path
  -> JS 同步更新资料页和导航头像
```

## 7. `api_admin.py`：管理员用户管理

文件：[app/routers/api_admin.py](../app/routers/api_admin.py)，统一前缀 `/api/admin/users`。

这些端点都依赖 `AdminUser`。执行顺序是“先确认身份，再检查管理员角色”；未登录返回 401，已登录但不是管理员返回 403。

| 方法与路径 | 从哪里触发 | 主要工作 |
| --- | --- | --- |
| `GET /api/admin/users` | `admin.js` 打开用户管理页 | 分页返回后台用户列表 |
| `POST /api/admin/users` | 后台新增用户表单 | 管理员创建普通用户或管理员；用户名/邮箱冲突返回 409 |
| `PATCH /api/admin/users/{id}` | 后台用户编辑表单 | 修改用户名、昵称、邮箱、角色；禁止管理员撤销自己的管理员身份 |
| `DELETE /api/admin/users/{id}` | 后台删除按钮 | 删除指定用户；禁止管理员删除自己 |

后台 HTML 页面本身可被直接打开，但数据 API 仍由 `AdminUser` 保护。前端隐藏内容只改善体验，不是安全边界。

## 8. `api_posts.py`：文章 CRUD、搜索和图片

文件：[app/routers/api_posts.py](../app/routers/api_posts.py)，统一前缀 `/api/posts`。

| 方法与路径 | 从哪里触发 | 主要工作 |
| --- | --- | --- |
| `POST /api/posts/images` | Toast UI 编辑器插入图片 | 要求管理员；校验并保存文章图片，返回可访问 URL |
| `POST /api/posts` | 发帖页提交 | 要求管理员；作者 ID 只能来自 Token；验证分类并创建文章 |
| `GET /api/posts` | 后台文章列表、API 调用 | 按关键词、分类和分页查询，返回文章、作者、分类、浏览数 |
| `GET /api/posts/search` | 全局搜索框输入后 300ms 防抖触发 | 只按标题搜索少量结果，减少每次键盘输入的响应体积 |
| `GET /api/posts/{id}` | API/后台读取单篇文章 | 查询文章，不存在返回 JSON 404 |
| `PATCH /api/posts/{id}` | 编辑文章提交 | 要求管理员；只更新实际传入字段，分类不存在返回 404 |
| `DELETE /api/posts/{id}` | 后台文章删除按钮 | 要求管理员；删除文章并提交事务，关联评论/活动由外键清理 |

搜索入口链：

```text
搜索框 input
  -> search.js 等待 300ms
  -> GET /api/posts/search?keyword=...
  -> PostTitleSearchParams 校验
  -> posts.search_post_titles()
  -> 返回 id/title
  -> 点击结果跳转 GET /posts/{id}
```

## 9. `api_activities.py`：浏览、点赞、收藏和个人历史

文件：[app/routers/api_activities.py](../app/routers/api_activities.py)。这个 Router 的路径分成文章互动 `/api/posts/...` 和个人历史 `/api/me/...`，所以没有统一 prefix。

| 方法与路径 | 从哪里触发 | 认证 | 主要工作 |
| --- | --- | --- | --- |
| `GET /api/posts/{id}/interaction` | API 调用或详情状态读取 | 可选 | 返回浏览、点赞、收藏数量；登录时附带本人状态 |
| `POST /api/posts/{id}/view` | `post-activities.js` 每次详情页加载 | 可选 | 浏览量原子加一；登录用户新增或更新足迹 |
| `POST /api/posts/{id}/like` | 详情页“点赞”按钮 | 必须登录 | 有点赞则删除，无点赞则新增，返回最新计数 |
| `POST /api/posts/{id}/favorite` | 详情页“收藏”按钮 | 必须登录 | 有收藏则删除，无收藏则新增，返回最新计数 |
| `GET /api/me/activities/posts?kind=...` | 我的活动页“赞过/收藏/足迹”标签 | 必须登录 | JOIN 行为表与文章表，按最近操作倒序返回 |
| `GET /api/me/activities/comments` | 我的活动页“评论”标签 | 必须登录 | JOIN 评论与文章，返回评论正文和文章上下文 |

足迹分支：

```text
POST /view
  -> OptionalCurrentUser：游客得到 None，登录用户得到 User
  -> _post_or_404()
  -> record_view()
  -> posts.view_count 原子 +1
  -> 游客：不写 post_views
  -> 登录用户：查询 user_id + post_id
       -> 没有足迹：add(PostView)
       -> 已有足迹：更新 viewed_at
  -> commit + refresh
  -> 返回最新互动状态
```

## 10. `comments.py`：历史评论和 WebSocket

文件：[app/routers/comments.py](../app/routers/comments.py)，统一前缀 `/api/posts/{post_id}/comments`。

| 协议与路径 | 从哪里触发 | 主要工作 |
| --- | --- | --- |
| `GET /api/posts/{id}/comments` | `comments.js` 打开文章详情 | 返回数据库历史评论；不要求 WebSocket 已连接 |
| `WS /api/posts/{id}/comments/ws` | `comments.js` 的 `connect()` | 接受连接、首消息认证、注册房间、接收评论、写库后广播、断线清理 |

WebSocket 完整入口：

```text
文章详情加载 comments.js
  -> loadHistory() 发送 HTTP GET
  -> connect() 创建 WebSocket
  -> FastAPI comment_websocket()
  -> websocket.accept()：只完成网络握手
  -> 浏览器发送 authenticate + Token
  -> _authenticate_websocket() 验证 JWT 和数据库用户
  -> comment_connections.register()：加入文章房间
  -> 浏览器提交 comment.create
  -> _create_comment_from_message()
  -> comments.create_comment() 提交数据库
  -> comment_connections.broadcast() 广播已提交评论
  -> WebSocketDisconnect 或页面离开
  -> finally 中 disconnect() 清理连接
```

为什么断线后前端可能再次 `connect()`：异常断网不等于用户主动离开页面。前端会有限次数重连以恢复实时评论；页面关闭时设置主动关闭标志，不再重连。

## 11. 常见 Depends 参数

| 参数 | 来自哪里 | Router 得到什么 |
| --- | --- | --- |
| `session: DbSession` | `Depends(get_db)` | 当前请求使用的 `AsyncSession` |
| `user: CurrentUser` | Bearer Token -> JWT -> users 表 | 已认证 `User`；失败直接 401，端点函数不会执行 |
| `user: OptionalCurrentUser` | 可选 Bearer Token | 游客为 `None`；有效 Token 为 `User`；无效 Token 仍返回 401 |
| `user: AdminUser` | `CurrentUser` 后再检查 `is_admin` | 管理员 `User`；普通用户 403 |
| `data: SomeSchema` | JSON 请求体 | 已经过 Pydantic 校验的数据对象；失败 422，端点函数不会执行 |

## 12. 如何找一个按钮最终进入哪个 Router

推荐从前端文本或 `data-*` 属性反向搜索：

```powershell
rg -n "data-like-post|/like" app
rg -n "data-profile-form|/api/users" app
rg -n "new WebSocket|comments/ws" app
```

阅读顺序：

```text
模板里的按钮/data 属性
  -> 对应 static/js 事件监听
  -> ajaxRequest/uploadFile/WebSocket 中的路径
  -> routers 中相同路径的装饰器
  -> Router 函数参数里的 Depends 和 Schema
  -> Router 调用的 Service
  -> Service 使用的 Model
  -> 测试中的成功和失败场景
```

## 13. 状态码在哪一层产生

- `422`：通常是 Schema、Query 或路径参数校验失败，Router 函数可能还没执行。
- `401`：认证依赖无法确认用户身份，Router 函数不会继续执行。
- `403`：身份有效，但角色或资源权限不够。
- `404`：Router 的 `_get_*_or_404()` 没查到资源。
- `409`：用户名或邮箱等唯一数据冲突。
- `200/201`：Service 成功完成，Router 用 `success_response()` 包装统一响应。

## 14. Router 不应该负责什么

Router 应保持为清晰入口，不应承担：

- 大段 SQL 查询或复杂业务分支。
- 自行创建另一个数据库 Session。
- 密码哈希、图片签名识别等可复用业务。
- 把 ORM 内部字段或密码哈希直接返回给客户端。
- 用前端隐藏按钮代替后端权限检查。

记忆方式：Router 负责“这个 HTTP/WebSocket 请求能不能进、输入是什么、成功返回什么”；Service 负责“业务具体怎么做”；Model 负责“数据在数据库里长什么样”。
