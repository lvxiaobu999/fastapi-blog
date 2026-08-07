# Redis Refresh Token 源码与完整流程详解

本文面向第一次接触 Token、Redis 和异步认证的读者，围绕
`app/services/refresh_sessions.py` 解释“为什么这样设计”，而不只是翻译代码。

## 1. 先理解要解决的用户需求

用户登录成功后，希望在 Access Token 过期时不用重新输入密码。同时系统还要支持：

- 用户退出后，不能继续用旧 Refresh Token 换新身份；
- 修改密码后，其他设备上的长期登录也要失效；
- 被窃取的 Refresh Token 不能无限重复使用；
- 过期会话自动清理，不让数据一直增长；
- Redis 泄露时，攻击者不能直接把存储值复制到 Cookie 中登录。

所以当前系统使用两种 Token：

| 凭证 | 作用 | 保存位置 | 服务端状态 |
| --- | --- | --- | --- |
| Access Token | 调用发帖、评论等受保护 API | 前端 localStorage | JWT 本身无会话记录 |
| Refresh Token | Access 过期后换取新 Access | HttpOnly Cookie | Redis 保存可撤销会话 |

Access Token 像短期门票，Refresh Token 像可以重新领取门票的长期凭证。长期凭证的权限
更大，因此必须支持轮换、过期和服务端撤销。

## 2. 为什么 Refresh Token 不是 JWT

当前 Refresh Token 通过：

```python
secrets.token_urlsafe(48)
```

生成。它只是高熵随机字符串，不携带 `user_id`、权限或过期时间。服务端必须查 Redis 才能
知道它属于谁、是否过期。这样带来一个重要能力：删除 Redis Key 就能让它立即失效。

如果 Refresh Token 也只使用完全无状态 JWT，服务端签发后很难在到期前主动撤销；退出、
改密和删除用户都更难做到即时控制。

## 3. 原始 Token、摘要和会话数据分别是什么

假设浏览器拿到的原始 Token 是：

```text
random-secret-token
```

系统不会把它原样存进 Redis，而是执行：

```text
SHA-256("random-secret-token") -> 64 位十六进制摘要
```

随后形成单会话 Key：

```text
<项目>:<环境>:auth:refresh:<token摘要>
```

对应 Value 是 JSON：

```json
{
  "user_id": 12,
  "created_at": "2026-08-07T10:00:00+00:00",
  "expires_at": "2026-08-14T10:00:00+00:00",
  "last_activity_at": "2026-08-07T10:00:00+00:00"
}
```

浏览器和 Redis 各自持有的信息如下：

```text
浏览器 Cookie                         Redis
原始 Refresh Token  --SHA-256-->     摘要组成的 Key -> 会话 JSON
```

只有浏览器提交原始 Token，服务端重新计算出相同摘要，才能找到会话。

### 为什么 `_digest()` 使用 SHA-256

`_digest()` 的目标不是加密后再解密，而是生成不可逆、固定长度的查找标识：

```python
hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
```

- `encode("utf-8")`：哈希算法处理字节，而 Python 的 Token 是字符串；
- `sha256(...)`：计算 256 位摘要；
- `hexdigest()`：把二进制摘要变成 64 个十六进制字符，方便组成 Redis Key。

这里不用 Argon2。Argon2 是为了抵抗“人类短密码”的字典穷举，故意消耗较多 CPU；Refresh
Token 是由安全随机源生成的长随机值，本身不存在短密码字典问题，SHA-256 足够且查找更快。

摘要也不是万能保护：如果原始 Token 已从浏览器或网络中泄露，攻击者仍可使用它。因此还
需要 HTTPS、HttpOnly Cookie、短期 Access Token、Refresh 轮换和过期策略共同防护。

## 4. 为什么还要维护用户会话 Set

单会话数据只能完成：

```text
已知 Token -> 找到该会话
```

修改密码时系统只有 `user_id`，并不知道用户在电脑、手机等设备上的原始 Token。为支持
“退出所有设备”，系统额外维护一个 Redis Set：

```text
<项目>:<环境>:auth:user:<user_id>:refresh-sessions
  -> digest-A
  -> digest-B
  -> digest-C
```

这相当于反向索引：

```text
Token 摘要 -> 单个会话
用户 ID    -> 该用户所有 Token 摘要
```

Redis Set 的特点是成员不重复，并提供：

- `SADD`：加入一个摘要；
- `SREM`：移除一个摘要；
- `SMEMBERS`：取出全部摘要。

## 5. `RefreshSessionData` 四个字段为什么存在

### `user_id`

Refresh Token 自身没有用户信息。刷新成功后，Router 使用 `user_id` 查询数据库，确认用户
仍存在，然后才签发新 Access JWT。

### `created_at`

记录这条长期会话最初何时建立。每次轮换继承原值，不把一次 Refresh 当成全新登录。

### `expires_at`

绝对截止时间。即使用户不断刷新，也不能超过它：

```text
首次登录 ------------------------------ 绝对过期
          刷新 -> 刷新 -> 刷新            必须重新登录
```

### `last_activity_at`

最近一次成功 Refresh 的时间。若连续超过空闲期限没有刷新，会话提前失效。普通 Access API
请求不会更新该字段，因为普通请求不访问 Redis。

## 6. JSON、dataclass 和 `frozen=True`

Redis String 最终保存字符串或字节，不能直接保存 Python 对象。`to_json()` 使用：

```text
dataclass -> asdict() -> json.dumps() -> Redis String
```

读取时通过 `json.loads()` 和 `from_json()` 反向恢复。字段缺失、JSON 损坏、时间格式非法时，
代码会删除损坏会话并拒绝刷新，这叫 fail closed：安全数据无法确认时默认拒绝，而不是放行。

`@dataclass(frozen=True)` 表示对象创建后字段不能被直接改写。轮换时构造一个新对象，更容易
看清哪些字段继承、哪些字段更新。

## 7. `_redis_text()` 解决什么问题

Redis 协议返回的是字节。项目客户端配置了 `decode_responses=True`，运行时通常直接得到
`str`；但 redis-py 的类型声明仍保守地认为可能返回 `bytes | str`。

```python
def _redis_text(value: bytes | str) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value
```

这个函数同时解决两个问题：

- 静态类型检查器知道转换后一定是 `str`；
- 即使将来某个 Redis 客户端关闭自动解码，运行时仍能正确处理 `bytes`。

它比 `cast(str, value)` 更可靠，因为 `cast` 只告诉检查器“相信我是 str”，不会真的转换值。

## 8. TTL 为什么既有时间字段又有 Redis 过期时间

Redis 写入会话时使用 `SET ... EX <秒数>`。TTL 到期后 Redis 自动删除 Key，避免像数据库表
一样需要定时清理过期行。

JSON 中仍保存 `expires_at`，原因是：

- 业务代码需要明确校验绝对截止时间；
- 轮换新 Key 时要继承原截止时间并重新计算剩余 TTL；
- 调试时可以解释会话为什么过期。

`_remaining_seconds()` 使用 `math.ceil()` 向上取整。若还剩 0.2 秒，直接转整数会变成 0，
Redis 的过期参数无法表达“还有不足一秒”；向上取整为 1 秒可避免提前删除。

## 9. 为什么创建会话使用 Lua

创建会话需要同时完成：

```text
SET 单会话 Key
SADD 用户会话索引
EXPIRE 用户会话索引
```

若拆成独立网络命令，程序可能在第一步后断线，留下“存在会话但索引找不到它”的半完成
状态。Redis Lua 脚本在服务端连续执行，中间不会插入其他命令，对外表现为一个原子操作。

`redis.eval(script, 2, ...)` 中的 `2` 表示后面两个参数是 Redis Key：

```text
KEYS[1] = 单会话 Key
KEYS[2] = 用户会话集合 Key
ARGV[1] = 会话 JSON
ARGV[2] = TTL
ARGV[3] = Token 摘要
```

Key 必须通过 `KEYS` 传递，普通数据通过 `ARGV` 传递；不要把值直接拼进 Lua 字符串。

## 10. 为什么轮换必须是原子的

若两个刷新请求几乎同时携带同一个旧 Token：

```text
请求 A：看到旧 Token 存在
请求 B：也看到旧 Token 存在
请求 A：生成新 Token A
请求 B：生成新 Token B
```

这会让一次性 Token 被重复使用。当前 Lua 脚本把以下操作绑定在一起：

```text
检查旧 Key存在
-> 删除旧 Key
-> 从用户 Set 移除旧摘要
-> 使用 SET NX 创建新 Key
-> 把新摘要加入用户 Set
```

请求 A 删除旧 Key 后，请求 B 执行脚本时会得到“不存在”，因此只有一个请求成功。这既是
并发控制，也是 Refresh Token Rotation 的防重放设计。

`NX` 表示新 Key 不存在时才允许写入，避免极小概率冲突覆盖已有会话。

## 11. Pipeline 和 Lua 有什么区别

二者都能减少网络往返，但目的不同：

- Pipeline：把多条命令批量发送；`transaction=True` 使用 `MULTI/EXEC` 一起提交；
- Lua：还能在 Redis 内部根据前一步结果执行条件分支，适合“检查后再修改”。

轮换需要根据旧 Key 是否存在决定后续动作，所以用 Lua。退出只需要删除已知会话并更新
索引，不需要复杂条件，因此使用事务 Pipeline 更容易阅读。

## 12. 登录链路如何打通

```text
前端提交账号密码
-> POST /api/auth/token
-> api_auth.login()
-> api_auth._authenticate()
-> auth.authenticate_user() 查询用户并验证 Argon2 密码
-> refresh_sessions.create_refresh_session()
   -> 生成原始随机 Token
   -> SHA-256 生成摘要
   -> Lua 写单会话和用户索引，并设置 TTL
-> auth.create_access_token() 签发 Access JWT
-> JSON 响应返回 Access Token
-> Set-Cookie 返回原始 Refresh Token
```

如果 Redis 不可用，创建 Refresh Session 会失败，统一异常处理器返回 503。系统不会只发
Access Token 却漏掉 Refresh Session，因为那会形成行为不完整的登录。

## 13. 普通受保护请求为什么不查 Redis

```text
Authorization: Bearer <Access JWT>
-> OAuth2PasswordBearer 提取 Token
-> verify_access_token() 校验签名、iat、exp、sub
-> get_current_user() 按 sub 查询 users 表
-> Router 执行业务和权限判断
```

这条链路不读取 Refresh Cookie，也不访问 Redis。Redis 暂时故障时，已经持有有效 Access
Token 的用户仍能继续访问，直到 Access Token 自己过期。

## 14. Access 过期后的刷新链路

```text
受保护 API 返回 401
-> 前端 POST /api/auth/refresh
-> 浏览器自动携带 HttpOnly Refresh Cookie
-> api_auth.refresh()
-> rotate_refresh_session()
   -> 摘要定位旧 Redis Key
   -> 读取并解析会话 JSON
   -> 检查绝对过期和空闲过期
   -> 生成新原始 Token 和摘要
   -> Lua 原子删除旧会话并创建新会话
-> Router 查询 users 表确认用户仍存在
-> 签发新 Access JWT
-> Set-Cookie 覆盖为新 Refresh Token
-> 前端重试原请求
```

轮换继承 `created_at` 和 `expires_at`，只更新 `last_activity_at`，所以持续刷新不会突破绝对
期限。旧 Cookie 在成功使用一次后立即失效。

## 15. 退出登录链路

```text
POST /api/auth/logout
-> Cookie 提供当前原始 Refresh Token
-> SHA-256 找到会话
-> 读取 user_id
-> Pipeline 删除会话 Key并从用户 Set 移除摘要
-> Router 删除浏览器 Cookie
-> 前端删除 localStorage 中的 Access Token
```

重复退出不会报错，这叫幂等。需要注意：已签发的无状态 Access JWT 无法通过删除 Redis
Key 立即撤销，因此前端主动删除它，服务端风险窗口由较短的 Access 有效期控制。

## 16. 修改密码和删除用户链路

```text
修改密码或管理员删除用户
-> revoke_user_refresh_sessions(user_id)
-> SMEMBERS 取得该用户全部摘要
-> 摘要转换为全部单会话 Key
-> Pipeline 删除所有单会话 Key和用户索引
```

所有设备之后都不能刷新。修改密码后现有 Access JWT 仍可能存活到 `exp`；删除用户后，
`get_current_user()` 查询不到数据库用户，因此旧 Access JWT 会立即得到 401。

## 17. Cookie 属性分别保护什么

- `HttpOnly=True`：JavaScript 不能读取 Refresh Token，降低 XSS 窃取风险；
- `Secure=True`：生产环境只通过 HTTPS 发送；
- `SameSite=Lax`：降低跨站请求自动携带 Cookie 的风险；
- `Path=/api`：只向 API 路径发送 Cookie；
- `Max-Age`：浏览器端过期时间与服务端 Refresh 期限对应。

HttpOnly 不是“Cookie 不会发送”，而是“浏览器会自动发送，但 JavaScript 读不到”。

## 18. 各函数的阅读顺序

建议按下面顺序阅读源码：

1. `RefreshSessionData`：先看一条会话保存什么；
2. `_digest()`：理解原始凭证和服务端摘要的边界；
3. `_session_key()`、`_user_sessions_key()`：理解两个方向的索引；
4. `create_refresh_session()`：理解登录怎样建立状态；
5. `rotate_refresh_session()`：理解过期、轮换和防重放；
6. `revoke_refresh_session()`：理解当前设备退出；
7. `revoke_user_refresh_sessions()`：理解全设备撤销；
8. 最后再读 Lua，因为此时已经知道它要保护哪些业务不变量。

## 19. 常见疑问

### Redis 中为何不存 Access Token

因为当前方案刻意让 Access JWT 无状态，普通业务请求不依赖 Redis。Redis 只管理需要主动
撤销的长期 Refresh Session。

### 为什么不直接用 user_id 作为唯一 Key

一个用户可能同时登录电脑和手机。只用 user_id 会让后一次登录覆盖前一次，也无法只退出
当前设备，因此每个 Token 必须有独立会话 Key。

### Redis 重启是否会让用户退出

取决于 AOF/RDB 持久化和数据是否恢复。会话数据丢失不会丢失用户、帖子等业务数据，但所有
Refresh Token 会失效，用户需要重新登录。当前 Docker 开发配置启用了 AOF。

### 为什么 Refresh 接口还要查询 users 表

Redis 只保存最小 `user_id`，不能证明用户此刻仍存在。签发新 Access JWT 前查库，可以避免
已经删除的用户靠残留 Redis 会话恢复身份。

## 20. 调试时怎样观察 Redis

进入 Redis CLI：

```powershell
docker compose -f compose.redis.development.yaml exec redis redis-cli
```

使用 `SCAN` 查找开发环境认证 Key，不要在生产使用阻塞式 `KEYS *`：

```redis
SCAN 0 MATCH *:auth:* COUNT 100
```

查看类型和剩余 TTL：

```redis
TYPE <key>
TTL <key>
GET <单会话key>
SMEMBERS <用户会话集合key>
```

不要把完整 Cookie、Access JWT、Redis 密码或生产连接地址复制到日志和截图中。

## 21. 关键文件地图

| 文件 | 它回答的问题 |
| --- | --- |
| `app/routers/api_auth.py` | HTTP 请求从哪里进入，Cookie 在哪里读写？ |
| `app/services/auth.py` | 密码怎样验证，Access JWT 怎样签发和验证？ |
| `app/services/refresh_sessions.py` | Refresh Session 怎样创建、轮换和撤销？ |
| `app/db/redis.py` | Redis 客户端怎样创建、注入和关闭？ |
| `app/dependencies/auth.py` | Bearer Token 怎样转换为当前用户和管理员权限？ |
| `app/exception_handlers.py` | Redis 故障为什么转换为安全的 503？ |
| `app/static/js/api.js` | Access 过期后前端怎样发起 Refresh 并重试？ |
| `tests/test_auth.py` | 登录、轮换、防重放、退出和 bytes 解码怎样验证？ |

