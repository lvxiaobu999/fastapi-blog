# JWT 与 Redis Refresh Token 认证流程

## 1. 当前认证结构

```text
Access Token
  -> JWT，无状态
  -> 前端 localStorage 保存
  -> Authorization: Bearer <token>
  -> 短期有效，生产建议 15 分钟

Refresh Token
  -> 高熵随机字符串，不是 JWT
  -> 浏览器 HttpOnly Cookie 保存
  -> Redis 只保存 SHA-256 摘要和最小会话数据
  -> 有绝对期限、空闲期限、轮换和撤销能力
```

Access JWT 的“无状态”表示服务端不为每个 Access Token 保存会话记录。当前依赖仍会根据 JWT `sub` 查询 users 表，以便用户删除或管理员权限变化立即生效；这不是 Access Token 会话存储。

## 2. 登录链路

```text
auth.js
  -> POST /api/auth/token
  -> api_auth._authenticate()
  -> services.auth.authenticate_user() 校验 Argon2 密码
  -> services.refresh_sessions.create_refresh_session()
  -> Redis SET 会话 Key + EX TTL
  -> services.auth.create_access_token()
  -> JSON 返回 Access JWT
  -> Set-Cookie 写入 Refresh Token
```

Redis Key 只使用原始 Token 的 SHA-256 摘要：

```text
fastapi-blog:auth:refresh:<sha256>
```

Value 包含 `user_id`、`created_at`、`expires_at`、`last_activity_at`。用户会话索引用于改密或删用户时撤销全部设备：

```text
fastapi-blog:auth:user:<user_id>:refresh-sessions
```

## 3. 受保护请求链路

```text
Authorization Bearer JWT
  -> verify_access_token() 校验签名、iat、exp、sub
  -> session.get(User, user_id)
  -> Router 权限判断
```

普通受保护请求不会访问 Redis，也不会刷新 Redis 会话活动时间。这保证 Access Token 链路不会因 Redis 延迟而变慢；`last_activity_at` 只在真正执行 Refresh 时更新，因此空闲期限必须明显长于 Access Token 有效期。

## 4. Refresh 轮换链路

```text
POST /api/auth/refresh
  -> 浏览器自动携带 HttpOnly Cookie
  -> SHA-256 定位 Redis Key
  -> 检查绝对过期和空闲过期
  -> 生成新随机 Token
  -> Redis Lua 原子执行：删除旧 Key + 写新 Key + 更新用户索引
  -> 查询 users 表确认用户仍存在
  -> 返回新 Access JWT，并轮换 Cookie
```

轮换继承首次登录的 `expires_at`，持续刷新不会无限延长绝对寿命。并发使用同一个旧 Token 时，Lua 脚本保证只有第一个请求成功，后续请求因旧 Key 已删除而返回 401。

## 5. 退出、改密和删用户

- 退出：删除当前 Refresh Session Key 和浏览器 Cookie。
- 修改密码：删除该用户 Redis 索引中的全部 Refresh Session，前端同步清理 localStorage Access Token并要求重新登录。
- 管理员删除用户：撤销全部 Refresh Session；旧 Access JWT 查询不到用户，因此立即返回 401。

无状态 Access JWT 无法通过 Redis立即撤销。普通退出后，泄露的 Access Token 理论上仍能使用到自身 `exp`；因此生产 Access Token 必须保持短期。若未来业务要求“立刻撤销每一个 Access Token”，需要增加 denylist、token version 或改成有状态 Access Session，这会牺牲无状态优势。

## 6. Cookie 安全

Refresh Cookie 使用：

```text
HttpOnly=true    JavaScript 无法读取
Secure=true      生产只通过 HTTPS 发送
SameSite=Lax     降低跨站请求携带风险
Path=/api        缩小 Cookie 发送范围
Max-Age          与 Refresh 绝对期限一致
```

生产环境必须配置 HTTPS 和 `AUTH_COOKIE_SECURE=true`。若未来改为跨站前后端并使用 `SameSite=None`，必须额外实现严格 CSRF Token 和 Origin 校验。

## 7. Redis 故障行为

Redis 不可用时：

- 公开读取和已有有效 Access JWT 的接口仍可工作。
- 登录、Refresh、退出、改密等需要会话写入的操作返回 503。
- 不会退化成签发没有 Refresh Session 的不完整登录。
- 服务端记录带 request_id 的完整 Redis 异常，客户端不看到连接地址或凭据。

## 8. 配置

```dotenv
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX=fastapi-blog:development
REDIS_SOCKET_TIMEOUT_SECONDS=2

ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_IDLE_TIMEOUT_MINUTES=1440
REFRESH_TOKEN_EXPIRE_MINUTES=10080
```

生产建议 Access 15 分钟，Refresh 最长 7～30 天，空闲期限根据产品体验选择 1～7 天。`REDIS_URL` 可能包含密码，应由部署平台 Secret 注入；跨不可信网络使用 `rediss://`。

## 9. 数据库迁移

旧迁移 `20260724_01_create_refresh_sessions.py` 仍保留历史，不应修改。新迁移：

```text
c56c4d6eeba7_drop_refresh_sessions_table.py
```

升级会删除旧 `refresh_sessions` 表，导致现有登录会话全部失效，但不会删除用户和帖子。部署顺序应先准备 Redis，再发布新代码并应用迁移。

## 10. 关键文件

| 文件 | 职责 |
| --- | --- |
| `app/services/auth.py` | 密码校验、Access JWT 签发和验证 |
| `app/services/refresh_sessions.py` | Redis Refresh 创建、轮换、单会话/全用户撤销 |
| `app/db/redis.py` | 异步 Redis 连接池和依赖 |
| `app/routers/api_auth.py` | Cookie 与 HTTP 契约 |
| `app/dependencies/auth.py` | Bearer JWT 当前用户与管理员校验 |
| `tests/test_auth.py` | 登录、轮换、防重放、退出和授权测试 |
