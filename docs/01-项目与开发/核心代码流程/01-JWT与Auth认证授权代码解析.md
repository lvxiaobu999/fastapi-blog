# JWT 与 Auth 认证授权代码解析

## 1. 先区分四个概念

| 概念 | 本项目中的实现 | 回答的问题 |
|---|---|---|
| 密码哈希 | Argon2 / `pwdlib` | 数据库怎样避免保存明文密码？ |
| 认证 Authentication | 密码校验、JWT 校验 | 当前请求者是谁？ |
| 授权 Authorization | `is_admin`、资源所有权 | 这个用户能做当前操作吗？ |
| 会话续期 | Redis Refresh Session | Access Token 过期后怎样继续登录？ |

JWT 主要负责短期认证，不负责保存密码，也不直接表示管理员权限永远有效。管理员状态仍从数据库
读取。Refresh Session 在下一篇文档单独解析。

## 2. 相关文件和边界

| 文件 | 职责 |
|---|---|
| [services/auth.py](../../../app/services/auth.py) | 密码哈希/验证、查询登录用户、签发和验证 JWT |
| [routers/api_auth.py](../../../app/routers/api_auth.py) | 接收表单/Cookie、返回 HTTP 状态码和响应 |
| [dependencies/auth.py](../../../app/dependencies/auth.py) | 从 Bearer Header 取得 Token，并注入当前用户 |
| [schemas/auth.py](../../../app/schemas/auth.py) | 登录响应和改密请求的数据契约 |
| [services/users.py](../../../app/services/users.py) | 创建用户、暂存新密码哈希、提交用户数据 |
| [models/user.py](../../../app/models/user.py) | `users` 表和 `hashed_password` 字段 |
| [static/js/api.js](../../../app/static/js/api.js) | 保存 Access Token，并添加 Authorization Header |

## 3. 登录完整调用链

```text
浏览器提交 username/password
  -> POST /api/auth/token
  -> FastAPI 解析 OAuth2PasswordRequestForm
  -> api_auth._authenticate()
  -> auth.authenticate_user()
  -> SQLAlchemy 查询 User
  -> Argon2 验证密码
  -> auth.create_access_token()
  -> refresh_sessions.create_refresh_session()
  -> JSON 返回 Access Token
  -> Set-Cookie 返回 HttpOnly Refresh Token
```

下面逐步展开。

## 4. `_password_hash` 是什么

```python
_password_hash = PasswordHash.recommended()
```

`PasswordHash` 是 `pwdlib` 提供的密码哈希工具。`recommended()` 根据当前库推荐选择 Argon2。

前导下划线 `_password_hash` 表示模块内部对象。其他模块应该调用：

```python
await hash_password(password)
await verify_password(plain_password, hashed_password)
```

而不是直接操作 `_password_hash`。这样以后调整算法时，只改 Service 内部实现。

## 5. 密码哈希为什么使用工作线程

```python
return await to_thread.run_sync(_password_hash.hash, password)
```

逐项解释：

| 代码 | 含义 |
|---|---|
| `_password_hash.hash` | 要执行的同步函数，本处没有加括号，因此传的是函数本身 |
| `password` | 传给同步函数的参数 |
| `to_thread.run_sync(...)` | 把同步函数放到 AnyIO 工作线程运行 |
| `await` | 当前协程暂停，事件循环可以处理其他请求，等线程计算完成再继续 |

Argon2 故意消耗 CPU 和内存，让攻击者难以高速猜密码。如果直接在 FastAPI 事件循环里计算，
同一进程的其他异步请求也会被卡住。

数据库保存的内容类似：

```text
$argon2id$v=19$m=65536,t=3,p=4$随机盐$哈希结果
```

它包含算法、参数、盐和结果，但不包含可以直接还原的明文密码。

## 6. 为什么不能“重新哈希后比较字符串”

```python
await verify_password("password123", stored_hash)
```

Argon2 每次生成新的随机盐：

```text
hash("password123") -> 字符串 A
hash("password123") -> 字符串 B
```

虽然 A 和 B 不相等，但都能验证同一个密码。因此必须使用：

```python
_password_hash.verify(plain_password, hashed_password)
```

密码库会从已有哈希中读取盐和算法参数，再计算并比较。

## 7. `authenticate_user()` 逐步解析

函数签名：

```python
async def authenticate_user(
    session: AsyncSession,
    username: str,
    password: str,
) -> User | None:
```

变量含义：

| 变量 | 内容 |
|---|---|
| `session` | 当前请求注入的 SQLAlchemy 异步数据库会话 |
| `username` | OAuth2 表单字段，项目允许填写用户名或邮箱 |
| `password` | 用户本次提交的明文，只用于验证，不能保存或记录 |
| 返回 `User` | 身份和密码都正确 |
| 返回 `None` | 用户不存在或密码错误，故意不区分原因 |

第一步，规范化登录标识：

```python
normalized_identity = username.strip().lower()
```

- `strip()` 删除首尾空格。
- `lower()` 转为小写。
- 原字符串不被修改；Python 字符串不可变，返回的是新字符串。

第二步，构造 SQLAlchemy 查询：

```python
select(User).where(
    or_(
        func.lower(User.username) == normalized_identity,
        func.lower(User.email) == normalized_identity,
    )
)
```

对应的 SQL 思路是：

```sql
SELECT * FROM users
WHERE lower(username) = :identity
   OR lower(email) = :identity;
```

| API | 含义 |
|---|---|
| `select(User)` | 查询完整 User ORM 对象 |
| `.where(...)` | 增加筛选条件 |
| `or_(a, b)` | a 或 b 满足一个即可 |
| `func.lower(...)` | 让数据库执行 SQL `lower()` |
| `session.scalar(...)` | 执行查询，并取得第一行的第一个结果，这里就是 User 或 None |

第三步，处理用户不存在：

```python
if user is None:
    await verify_password(password, _DUMMY_PASSWORD_HASH)
    return None
```

`_DUMMY_PASSWORD_HASH` 不属于真实用户。它的作用是让“不存在用户”和“密码错误”都执行一次
耗时接近的 Argon2 校验，降低攻击者根据响应时间枚举账号的可能性。

第四步，验证真实用户密码：

```python
verified = await verify_password(password, user.hashed_password)
return user if verified else None
```

条件表达式等价于：

```python
if verified:
    return user
return None
```

## 8. JWT 到底是什么

JWT 通常由三段组成：

```text
header.payload.signature
```

示意：

```text
eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIiwiaWF0IjoxLCJleHAiOjJ9.签名
```

| 部分 | 内容 | 是否加密 |
|---|---|---|
| Header | 算法、Token 类型 | 否，只是 Base64URL 编码 |
| Payload | `sub`、`iat`、`exp` 等声明 | 否，只是 Base64URL 编码 |
| Signature | 使用服务端密钥计算的签名 | 用于防伪，不是加密正文 |

所以 JWT Payload 可以被客户端读取，不能放密码、邮箱隐私或 Secret。签名保证的是“内容没有被
不知道密钥的人修改”。

## 9. `create_access_token()` 逐步解析

```python
settings = get_settings()
issued_at = datetime.now(UTC)
expires_at = issued_at + timedelta(minutes=settings.access_token_expire_minutes)
```

| 变量 | 含义 |
|---|---|
| `settings` | 缓存的应用配置对象 |
| `issued_at` | 当前 UTC 签发时间 |
| `expires_at` | Token 截止时间 |
| `expires_delta` | 测试可传入的自定义有效期，正常请求不传 |

Payload：

```python
payload = {
    "sub": str(user_id),
    "iat": issued_at,
    "exp": expires_at,
}
```

| 声明 | 全称 | 作用 |
|---|---|---|
| `sub` | subject | Token 属于哪个用户，本项目保存用户主键字符串 |
| `iat` | issued at | Token 何时签发 |
| `exp` | expiration | 何时过期，PyJWT 解码时会自动检查 |

签名：

```python
jwt.encode(
    payload,
    settings.secret_key.get_secret_value(),
    algorithm=settings.algorithm,
)
```

- `SecretStr` 平常打印时隐藏值；`get_secret_value()` 只在真正签名时取出原文。
- `algorithm` 当前是 HS256，签发和验证使用同一个 Secret。
- 返回值是最终 JWT 字符串。

## 10. `verify_access_token()` 逐步解析

```python
payload = jwt.decode(
    token,
    secret,
    algorithms=[settings.algorithm],
    options={"require": ["sub", "iat", "exp"]},
)
```

`jwt.decode()` 会依次完成：

1. 检查 Token 是否是合法三段结构。
2. 只允许服务端配置的算法，不能听信 Token 自己要求的算法。
3. 使用 Secret 重新计算签名并比较。
4. 检查 `exp` 是否已过期。
5. 检查 `sub`、`iat`、`exp` 是否都存在。
6. 成功后返回 Payload 字典。

最后：

```python
return int(payload["sub"])
```

JWT 中保存的是字符串，数据库主键是整数，所以转换为 `int`。字段缺失、格式错误、签名错误和
过期都会抛异常，交给 Depends 转为统一 401。

## 11. FastAPI 怎样提取 Bearer Token

```python
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/oauth2-token"
)
```

客户端 Header：

```http
Authorization: Bearer <access_token>
```

`OAuth2PasswordBearer` 会：

1. 查找 `Authorization` Header。
2. 检查方案是否为 Bearer。
3. 取出后面的 Token 字符串。
4. 缺失时自动拒绝请求。
5. 用 `tokenUrl` 告诉 Swagger 的 Authorize 按钮去哪里登录。

`tokenUrl` 不是每次验证都会访问的地址，只是 OpenAPI/OAuth2 元数据。

## 12. `Annotated` 和 `Depends` 是什么

```python
token: Annotated[str, Depends(oauth2_scheme)]
```

可以拆成两层理解：

- `str`：函数最终收到的值是字符串。
- `Depends(oauth2_scheme)`：这个值不由调用者手工传入，而由 FastAPI 执行依赖取得。

同理：

```python
CurrentUser = Annotated[User, Depends(get_current_user)]
```

Router 写：

```python
async def endpoint(current_user: CurrentUser):
    ...
```

FastAPI 实际先执行 `get_current_user()`，成功后把 User ORM 对象传给端点。

## 13. `get_current_user()` 为什么验证 JWT 后还要查数据库

```python
user_id = verify_access_token(token)
user = await session.get(User, user_id)
```

JWT 证明“服务端曾经给这个 user_id 签发 Token”，但用户可能已经被删除，管理员状态也可能已
改变。因此每个受保护请求继续查询数据库：

```text
JWT 有效 + 用户存在 -> 返回 User
JWT 有效 + 用户已删除 -> 401
JWT 无效或过期 -> 401
```

`session.get(User, user_id)` 按主键查找 User，找不到返回 `None`。

## 14. 认证和管理员授权的执行顺序

```python
async def require_admin(
    user: Annotated[User, Depends(get_current_user)]
) -> User:
```

FastAPI 的执行顺序：

```text
OAuth2PasswordBearer 提取 Token
  -> get_current_user 验证身份
  -> require_admin 检查 user.is_admin
  -> Router 端点
```

状态码：

| 情况 | 状态码 | 原因 |
|---|---:|---|
| 没有 Token、Token 无效、用户不存在 | 401 | 身份没有确认 |
| 身份有效，但 `is_admin=False` | 403 | 已知道是谁，但权限不足 |
| 管理员 | 继续执行 | 认证和授权都通过 |

## 15. OptionalCurrentUser 的用途

```python
optional_oauth2_scheme = OAuth2PasswordBearer(..., auto_error=False)
```

`auto_error=False` 表示没有 Header 时不自动返回 401，而是传入 `None`。

适合“游客可以访问，登录用户还能看到个人状态”的接口，例如文章互动状态：

```text
游客 -> 返回计数，liked=false
登录用户 -> 返回计数和本人是否点赞
伪造 Token -> 仍返回 401，不能当成游客降级
```

## 16. Router 登录端点逐步解析

```python
form: Annotated[OAuth2PasswordRequestForm, Depends()]
```

FastAPI 从 `application/x-www-form-urlencoded` 请求体解析 OAuth2 标准字段。它不是 JSON。

`_authenticate()`：

```python
user = await auth_service.authenticate_user(...)
refresh_token = await create_refresh_session(redis, user.id)
token = TokenResponse(access_token=create_access_token(user.id))
return token, refresh_token
```

返回类型：

```python
tuple[TokenResponse, str]
```

- 第一个元素是准备放入 JSON 的 Access Token 响应对象。
- 第二个元素是准备放入 HttpOnly Cookie 的 Refresh Token 字符串。

## 17. Refresh Cookie 每个参数的含义

```python
response.set_cookie(
    "refresh_token",
    refresh_token,
    httponly=True,
    secure=settings.auth_cookie_secure,
    samesite="lax",
    max_age=settings.refresh_token_expire_minutes * 60,
    path="/api",
)
```

| 参数 | 含义 |
|---|---|
| `httponly=True` | JavaScript 不能读取 Cookie，降低 XSS 窃取风险 |
| `secure=True` | 只通过 HTTPS 发送；生产环境强制开启 |
| `samesite="lax"` | 限制跨站请求自动携带 Cookie，降低 CSRF 风险 |
| `max_age` | 浏览器保留秒数，因此分钟配置乘以 60 |
| `path="/api"` | 只向 `/api` 路径发送，不发送给静态文件等无关请求 |

Access Token 返回给 JavaScript；Refresh Token 不进入 JSON，只由浏览器 Cookie 管理。

## 18. 为什么有两个登录端点

| 路径 | 调用者 | 响应形状 |
|---|---|---|
| `/api/auth/token` | 博客前端 | 项目统一 `ApiSuccess` 信封 |
| `/api/auth/oauth2-token` | Swagger OAuth2 | 顶层 `access_token/token_type` 标准形状 |

两者复用 `_authenticate()`，密码规则和 Token 签发不会出现两套实现。

## 19. 修改密码为什么先撤销 Redis 再提交数据库

```text
验证旧密码
  -> 只在 ORM 对象上暂存新哈希
  -> 撤销全部 Refresh Session
  -> 数据库 commit 新哈希
  -> 删除浏览器 Refresh Cookie
```

如果 Redis 先失败，数据库尚未提交，密码仍是旧值。这样不会出现“接口返回失败，但密码实际已经
修改且旧 Refresh Session 仍有效”的更危险状态。

注意：现有无状态 Access JWT 仍可使用到自身 `exp`。因此生产 Access Token 应保持较短。

## 20. 常见失败分支

| 位置 | 失败原因 | 结果 |
|---|---|---|
| 登录 | 用户不存在或密码错误 | 统一 401 |
| JWT decode | 格式、签名、过期、声明错误 | Depends 转为 401 |
| 当前用户查询 | JWT 对应用户已删除 | 401 |
| 管理员依赖 | 普通用户 | 403 |
| Refresh | Cookie 缺失、Redis 会话无效 | 401 |
| 修改密码 | 当前密码错误 | 400 |
| Redis 不可用 | 登录/刷新/改密无法管理会话 | 全局处理器返回 503 |

## 21. 调试建议

按下面顺序打断点：

1. `api_auth.login()`：确认表单是否解析成功。
2. `_authenticate()`：确认 User 是 `None` 还是对象。
3. `authenticate_user()`：观察规范化标识和数据库查询结果，不查看/输出明文密码。
4. `create_access_token()`：只观察 `sub/iat/exp`，不要复制完整 Token 到公开位置。
5. `get_current_user()`：确认 Header 提取、JWT 验证和数据库用户查询。
6. `require_admin()`：确认认证成功后才判断 `is_admin`。

测试入口：[tests/test_auth.py](../../../tests/test_auth.py)。

