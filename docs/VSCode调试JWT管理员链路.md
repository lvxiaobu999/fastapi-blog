# VSCode 调试 JWT 管理员链路

本文说明如何使用 VSCode 的 Python Debugger，逐步观察“登录后为什么是管理员、为什么普通用户不能发布帖子”。调试目标是理解请求如何经过 Router、Service、JWT 依赖和数据库，而不是修改代码绕过权限。

## 一、调试前准备

先确认依赖已安装，并确保开发环境指向你准备使用的数据库：

```powershell
uv sync
$env:ENV = "development"
$env:PYTHONPATH = (Get-Location).Path
```

确认数据库表结构已经迁移：

```powershell
uv run alembic current
uv run alembic check
```

如果还没有管理员，先执行：

```powershell
uv run python -m app.cli create-admin
```

这个命令创建的用户才会有 `is_admin=True`。公开注册页面创建的用户默认是普通用户。

## 二、VSCode 配置调试器

在项目根目录创建 `.vscode/launch.json`。如果 `.vscode` 已经存在，只添加下面这个配置，不要覆盖已有配置：

```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "FastAPI Blog",
      "type": "debugpy",
      "request": "launch",
      "module": "uvicorn",
      "args": [
        "app.main:app",
        "--reload",
        "--host",
        "127.0.0.1",
        "--port",
        "8000"
      ],
      "env": {
        "ENV": "development",
        "PYTHONPATH": "${workspaceFolder}"
      },
      "justMyCode": true
    }
  ]
}
```

说明：

- `module: uvicorn` 让 VSCode 用当前 Python 环境启动 ASGI 应用。
- `app.main:app` 表示导入 `app/main.py` 中名为 `app` 的 FastAPI 对象。
- `PYTHONPATH` 确保调试器能导入项目根目录下的 `app` 包。
- `--reload` 适合开发，但文件变化时会重启子进程；如果断点不稳定，先删除 `--reload`。
- `justMyCode: true` 会优先停在项目代码；需要查看 FastAPI 或 PyJWT 内部时再改为 `false`。

选择左侧“运行和调试”，选中 `FastAPI Blog`，按 `F5` 启动。浏览器或 API 客户端访问 `http://127.0.0.1:8000`。

## 三、推荐断点顺序

### 1. 登录接口

在 [api_auth.py](../app/routers/api_auth.py) 的 `login()` 设置断点：

```python
user = await auth_service.authenticate_user(session, form.username, form.password)
```

发送登录请求：

```http
POST http://127.0.0.1:8000/api/auth/token
Content-Type: application/x-www-form-urlencoded

username=你的管理员用户名&password=你的管理员密码
```

停下后观察“变量”面板：

- `form.username`：OAuth2 表单解析出来的用户名。
- `form.password`：明文密码，只用于当前验证，不能复制到日志或截图。
- `user`：查询到的 `User` ORM 对象。
- `user.is_admin`：这里应该是 `True`。

继续执行到：

```python
return TokenResponse(access_token=auth_service.create_access_token(user.id))
```

进入 `create_access_token()`，观察：

- `user_id`：管理员数据库主键。
- `issued_at`：UTC 签发时间。
- `expires_at`：过期时间。
- `payload`：通常是 `sub`、`iat`、`exp`，不包含密码或管理员状态。

不要在调试控制台打印完整 Token，也不要把 Token 提交到 Git。

### 2. Token 验证

登录成功后，前端会把 Token 放到当前标签页的 `localStorage`，发帖 AJAX 会发送：

```http
Authorization: Bearer <access_token>
```

在 [dependencies/auth.py](../app/dependencies/auth.py) 的以下位置设置断点：

```python
user_id = verify_access_token(token)
```

观察：

- `token`：从 Bearer Header 提取出的 JWT。
- `user_id`：解码并转换后的整数主键。

进入 [services/auth.py](../app/services/auth.py) 的 `verify_access_token()`，观察：

- `settings.algorithm`：应为 `HS256`。
- `payload["sub"]`：JWT 中保存的用户主键字符串。
- `payload["exp"]`：过期时间。
- `payload["iat"]`：签发时间。

`jwt.decode()` 如果发现签名错误、Token 过期、缺少必需字段或算法不允许，会抛出异常。依赖层会把这些异常统一转换成 `401`。

### 3. 当前用户查询

继续执行到：

```python
user = await session.get(User, user_id)
```

此时重点看：

- `user_id` 是否与登录时 `create_access_token()` 的 `user_id` 相同。
- `user` 是否为 `None`。
- `user.id`、`user.username` 和 `user.is_admin`。

这里必须查数据库，因为管理员状态以数据库当前值为准，不能把 `is_admin` 放入 JWT 后永久信任。

### 4. 管理员授权

在 `require_admin()` 设置断点：

```python
if not user.is_admin:
    raise HTTPException(...)
```

分支含义：

| 调试观察 | HTTP 结果 | 含义 |
| --- | --- | --- |
| 没有 Authorization Header | 401 | 没有完成认证 |
| Token 无效或过期 | 401 | 身份无法确认 |
| `user` 存在且 `is_admin=False` | 403 | 身份有效，但没有管理员权限 |
| `user` 存在且 `is_admin=True` | 继续执行 | 允许进入发帖 Router |

### 5. 发帖 Router

在 [api_posts.py](../app/routers/api_posts.py) 的 `create_post()` 设置断点：

```python
return await post_service.create_post(
    session, PostCreate(**data.model_dump(), user_id=current_user.id)
)
```

观察：

- `data` 只有标题和正文，不应包含客户端作者 ID。
- `current_user.id` 来自 JWT 的 `sub`，并已通过数据库查询确认。
- 新构造的 `PostCreate.user_id` 应等于 `current_user.id`。

如果能停在这里，说明认证和管理员授权已经成功；后续问题才属于帖子 Service 或数据库写入。

## 四、调试普通用户

用公开注册接口创建一个普通账号，或者直接登录一个 `is_admin=False` 的用户。发帖时在 `require_admin()` 观察：

```python
user.is_admin == False
```

程序会抛出：

```text
403 Admin access required
```

这不是 JWT 解码失败。Token 是有效的，失败发生在“授权”阶段。

## 五、调试前端 AJAX

打开浏览器开发者工具：

1. 进入 `Network`。
2. 提交登录表单，查看 `/api/auth/token`。
3. 确认请求的 `Content-Type` 是 `application/x-www-form-urlencoded`。
4. 确认响应包含 `access_token` 和 `token_type=bearer`。
5. 提交发布表单，查看 `/api/posts`。
6. 在 `Request Headers` 中确认存在 `Authorization: Bearer ...`。

如果登录成功但发帖返回 401，优先检查：

- `localStorage` 是否存在 `blog-access-token`。
- `api.js` 是否使用 `auth: true`。
- 浏览器请求是否真的发送 Authorization Header。
- Token 是否已经过期。

如果返回 403，说明 Header 和 JWT 基本有效，应在 VSCode 中检查数据库用户的 `is_admin`。

## 六、VSCode 常用调试操作

- `F5`：启动或继续调试。
- `F10`：单步跳过，执行当前行但不进入函数。
- `F11`：单步进入，进入当前调用的函数。
- `Shift+F11`：跳出当前函数。
- `F5`：在断点处继续运行。
- 调试控制台：查看表达式，例如 `user.is_admin` 或 `payload["sub"]`。
- 条件断点：右键断点，设置 `user is not None and user.is_admin is False`，只观察普通用户。
- 日志断点：只记录不暂停，但不要记录密码、完整 Token 或 SECRET_KEY。

## 七、常见问题定位表

| 现象 | 最先查看的位置 | 常见原因 |
| --- | --- | --- |
| 登录 422 | `api_auth.login` 之前 | 请求不是表单格式，缺少 username/password |
| 登录 401 | `authenticate_user` | 用户名不存在、密码错误或密码哈希不匹配 |
| 发帖 401 | `get_current_user` | Header 缺失、Token 过期、签名错误或用户已删除 |
| 发帖 403 | `require_admin` | 用户存在，但数据库 `is_admin=False` |
| 断点完全不命中 | `launch.json` 和启动方式 | 使用了别的终端进程、`--reload` 子进程或选错 Python 环境 |
| Token 每次都不同 | `create_access_token` | 正常现象；签发时间和签名不同，不要比较字符串是否固定 |

## 八、安全注意事项

- 不要在截图、日志、调试输出中暴露明文密码、密码哈希、SECRET_KEY 或完整 JWT。
- 不要为了调试直接把 `is_admin=True` 写进公开注册 Schema。
- 不要在 `verify_access_token()` 中关闭签名、过期或算法校验。
- 调试完成后停止 VSCode 调试进程，避免开发服务器继续占用数据库连接。
