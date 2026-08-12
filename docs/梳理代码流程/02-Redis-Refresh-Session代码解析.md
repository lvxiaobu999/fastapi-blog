# Redis Refresh Session 代码解析

## 1. 为什么 JWT 之外还需要 Redis

Access JWT 是无状态 Token：签发后，服务端只要验证签名和过期时间就能接受，不需要查询 Redis。
优点是快，缺点是服务端很难在过期前主动撤销。

本项目把登录凭证分成两层：

| 凭证 | 保存位置 | 有效期 | 主要用途 |
|---|---|---|---|
| Access Token | 前端 `localStorage` | 较短 | 调用普通受保护 API |
| Refresh Token | 浏览器 HttpOnly Cookie | 较长 | Access 过期后换取新 Access |
| Refresh Session | Redis | 与 Refresh 对应 | 服务端验证、轮换和主动撤销 |

普通文章请求只验证 Access JWT。只有登录、刷新、退出、改密和删号会操作 Redis Session。

## 2. 相关文件

| 文件 | 职责 |
|---|---|
| [db/redis.py](../../app/db/redis.py) | 创建共享异步 Redis 客户端和连接池 |
| [services/refresh_sessions.py](../../app/services/refresh_sessions.py) | Token、Redis Key、Lua 原子操作和会话规则 |
| [routers/api_auth.py](../../app/routers/api_auth.py) | 从 Cookie 读取/写入 Refresh Token |
| [static/js/api.js](../../app/static/js/api.js) | Access 401 时触发 Refresh single-flight |
| [tests/test_auth.py](../../tests/test_auth.py) | 会话创建、轮换、防重放、撤销和 bytes 兼容测试 |

## 3. Redis 客户端是怎样创建的

```python
redis_client = Redis.from_url(
    settings.redis_url.get_secret_value(),
    decode_responses=True,
    socket_connect_timeout=settings.redis_socket_timeout_seconds,
    socket_timeout=settings.redis_socket_timeout_seconds,
    health_check_interval=30,
)
```

逐项解释：

| 参数 | 作用 |
|---|---|
| `Redis.from_url(...)` | 根据 `redis://` 或 `rediss://` 地址创建客户端和连接池 |
| `get_secret_value()` | Redis URL 用 `SecretStr` 隐藏，连接时才取原值 |
| `decode_responses=True` | 把 Redis 返回的 bytes 自动解码为 str |
| `socket_connect_timeout` | 建立 TCP/TLS 连接最多等待多久 |
| `socket_timeout` | 已连接后单条 Redis 命令最多等待多久 |
| `health_check_interval=30` | 空闲连接重新使用时，定期用 PING 检查是否仍可用 |

连接池意味着每次请求不重新创建 TCP 连接。多个协程共享 `redis_client`，redis-py 从池中借出
连接，命令结束后归还。

## 4. `get_redis()` 为什么使用 `yield`

```python
async def get_redis() -> AsyncIterator[Redis]:
    yield redis_client
```

FastAPI Depends 支持带 `yield` 的依赖：`yield` 前是提供资源，`yield` 后可以写请求级清理。

这里客户端是应用级共享对象，请求结束时不关闭，所以 `yield` 后没有代码。应用真正退出时由：

```python
await redis_client.aclose()
```

统一关闭连接池。如果每个请求都关闭共享客户端，后续请求会无法继续使用。

## 5. Refresh Token 的真实结构

```text
v1.<user_id>.<session_id>.<secret>
```

示意，不是真实 Token：

```text
v1.7.device-random-id.one-time-random-secret
```

| 字段 | 是否秘密 | 作用 |
|---|---|---|
| `v1` | 否 | Token 格式版本，未来可支持新格式 |
| `user_id` | 否 | 定位属于哪个用户的 Redis Key |
| `session_id` | 不作为认证秘密 | 定位哪台设备/浏览器会话 |
| `secret` | 是 | 证明浏览器持有有效会话的高熵随机凭证 |

重要：能解析出 user_id 不代表认证成功。攻击者可以自己写一个 user_id，但不知道 Redis 中会话
对应的 secret，摘要比较会失败。

## 6. `RefreshTokenParts` dataclass

```python
@dataclass(frozen=True)
class RefreshTokenParts:
    user_id: int
    session_id: str
    secret: str
```

`@dataclass` 自动生成初始化函数，近似于：

```python
def __init__(self, user_id, session_id, secret):
    self.user_id = user_id
    self.session_id = session_id
    self.secret = secret
```

`frozen=True` 表示创建后不能重新赋值，避免验证过程中意外改写 Token 字段。

`raw_token` 是 property：

```python
@property
def raw_token(self) -> str:
    return f"v1.{self.user_id}.{self.session_id}.{self.secret}"
```

调用时写 `token.raw_token`，不写括号；它根据字段临时计算完整字符串。

## 7. 首次登录和刷新时 `_new_token()` 的区别

```python
session_id=session_id or secrets.token_urlsafe(24)
secret=secrets.token_urlsafe(48)
```

首次登录：

```python
_new_token(user_id=7)
```

没有旧 session_id，因此生成新的 session_id 和 secret。

刷新：

```python
_new_token(user_id=7, session_id=old.session_id)
```

继续使用稳定 session_id，只生成新 secret。

`secrets.token_urlsafe()` 使用操作系统安全随机源，生成适合 URL/Cookie 的字符串。它不同于
`random`，后者不适合生成认证凭证。

## 8. 为什么 Redis 只保存摘要

```python
hashlib.sha256(secret.encode("utf-8")).hexdigest()
```

步骤：

1. `secret` 是 Python str。
2. `.encode("utf-8")` 转为 SHA-256 接受的 bytes。
3. `hashlib.sha256(...)` 计算摘要对象。
4. `.hexdigest()` 返回 64 个十六进制字符。

浏览器保存原始 secret，Redis 只保存 digest。Redis 内容泄露后，摘要不能直接放进 Cookie 使用，
因为服务端会再次对 Cookie secret 做 SHA-256。

Refresh secret 本身有足够高的随机性，不像人类密码容易被字典猜测，所以使用快速 SHA-256，
不需要再用昂贵的 Argon2。

## 9. Redis 中实际保存什么

假设：

```text
env = production
redis_key_prefix = fastapi-blog:production
user_id = 7
session_id = abc123
```

主要 Key：

```text
fastapi-blog:production:production:{auth-user-7}:refresh:session:abc123
fastapi-blog:production:production:{auth-user-7}:refresh:sessions
fastapi-blog:production:production:{auth-user-7}:refresh:generation
```

当前配置前缀本身已经包含环境名，而 `_key_base()` 又追加 `settings.env`，所以示例会出现两次
`production`。这不影响正确性，只是 Key 命名结果；修改前缀会让所有旧会话不可见。

三个 Key 的类型：

| Key 后缀 | Redis 类型 | 内容 |
|---|---|---|
| `session:<session_id>` | String | 会话 JSON |
| `sessions` | Set | 当前用户的 session_id 集合 |
| `generation` | String 中的整数 | 用户会话版本，默认不存在时按 0 |

Session JSON 示意：

```json
{
  "user_id": 7,
  "session_id": "abc123",
  "token_digest": "64位SHA-256摘要",
  "generation": 0,
  "created_at": "2026-08-11T04:00:00+00:00",
  "expires_at": "2026-08-18T04:00:00+00:00",
  "last_activity_at": "2026-08-11T04:00:00+00:00"
}
```

## 10. `RefreshSessionData` 为什么转 JSON

Redis String 保存的是字节或文本，不能直接保存 Python dataclass。因此：

```python
json.dumps(asdict(self), separators=(",", ":"))
```

| API | 作用 |
|---|---|
| `asdict(self)` | dataclass 转为普通 dict |
| `json.dumps(...)` | dict 转为 JSON 字符串 |
| `separators=(",", ":")` | 去掉默认空格，减少 Redis 存储 |

读取时 `from_json()` 执行相反方向：

```text
Redis 字符串 -> json.loads -> dict -> RefreshSessionData
```

逐字段 `int()`、`str()` 转换让类型边界明确。字段缺失或内容损坏会抛异常，调用方按无效会话
处理，不相信不完整数据。

## 11. `_redis_text()` 为什么同时支持 bytes 和 str

```python
return value.decode("utf-8") if isinstance(value, bytes) else value
```

生产客户端配置了 `decode_responses=True`，通常返回 str；但 redis-py 类型声明和部分测试客户端
仍可能返回 bytes。Service 在入口统一转换，后续 JSON 解析不需要反复判断。

条件表达式等价于：

```python
if isinstance(value, bytes):
    return value.decode("utf-8")
return value
```

## 12. Redis Cluster hash tag 是什么

Redis Cluster 把不同 Key 分散到多个节点。Lua 脚本不能同时原子操作不同分片的 Key。

Redis 只使用花括号中的文本计算 slot：

```text
...:{auth-user-7}:refresh:session:abc
...:{auth-user-7}:refresh:sessions
...:{auth-user-7}:refresh:generation
```

三者花括号内容相同，因此属于同一分片。

这也是配置校验禁止 `REDIS_KEY_PREFIX` 包含 `{`、`}` 的原因：配置层额外花括号可能改变 Redis
真正采用的 hash tag，导致 Lua 报 `CROSSSLOT`。

## 13. TTL 是什么

TTL 是 Redis Key 剩余生存秒数。到期后 Redis 自动删除 Session，不需要定时任务逐条清理。

```python
math.ceil((expires_at - now).total_seconds())
```

- `expires_at - now` 得到 `timedelta`。
- `.total_seconds()` 转为秒，可以是小数。
- `math.ceil()` 向上取整，剩余 0.2 秒按 1 秒处理，不提前删除。
- `max(1, ...)` 保证传给 Redis 的 EX 至少为 1。

## 14. 为什么使用 Lua

如果 Python 分开执行：

```text
SET session
SADD sessions
EXPIRE sessions
```

进程可能在第一步后崩溃，留下 Session 却没有用户索引。两个刷新请求也可能同时读取旧 secret，
各自认为自己可以成功。

Redis Lua 脚本执行期间不会穿插另一个 Redis 命令，因此可以把多步操作变成一个原子动作。

调用形式：

```python
await redis.eval(script, numkeys, key1, key2, arg1, arg2)
```

`numkeys` 决定参数分组：

```text
前 numkeys 个参数 -> Lua KEYS[1], KEYS[2]...
剩余参数          -> Lua ARGV[1], ARGV[2]...
```

Lua 数组从 1 开始，不是 Python 的 0。

## 15. 创建会话脚本参数表

Python：

```python
redis.eval(
    _CREATE_SCRIPT,
    3,
    session_key,
    sessions_key,
    generation_key,
    session_json,
    ttl,
    session_id,
    generation,
)
```

映射：

| Lua 参数 | 实际值 |
|---|---|
| `KEYS[1]` | 单设备 Session Key |
| `KEYS[2]` | 用户 Session Set Key |
| `KEYS[3]` | 用户 generation Key |
| `ARGV[1]` | 会话 JSON |
| `ARGV[2]` | TTL 秒数 |
| `ARGV[3]` | session_id |
| `ARGV[4]` | Python 读取到的 generation |

脚本逻辑翻译成 Python 思路：

```text
读取当前 generation，不存在按 0
  -> 如果与 ARGV[4] 不同，返回 -1
  -> 如果 Session Key 已存在，返回 0
  -> SET Session JSON EX TTL
  -> SADD 用户 Set session_id
  -> 如果 Set TTL 更短，则延长到本 Session TTL
  -> 返回 1
```

为什么比较 generation：Python 读取 generation 后，用户可能恰好修改密码。Lua 再检查一次，避免
在全量撤销之后用旧 generation 创建新会话。

返回值：

| 返回值 | 含义 | Python 行为 |
|---:|---|---|
| `1` | 创建成功 | 返回 Token |
| `0` | session_id 冲突 | 重新生成 |
| `-1` | generation 已变化 | 读取新 generation 后重试 |

## 16. `create_refresh_session()` 完整步骤

```text
1. 读取 Settings
2. now = 当前 UTC
3. expires_at = now + Refresh 绝对有效期
4. 计算 TTL
5. 生成 session_id 和 secret
6. 读取用户 generation，不存在按 0
7. secret -> SHA-256 digest
8. 组装 RefreshSessionData
9. Lua 原子写 Session + Set
10. 成功后只把原始 Token 返回 Router
11. Router 写入 HttpOnly Cookie
```

最多重试 3 次，只处理 generation 并发变化或极小概率 ID 冲突。连续失败说明账户撤销操作竞争
异常频繁，抛 RuntimeError 比创建不确定会话更安全。

## 17. 刷新脚本怎样阻止旧 Token 重放

轮换脚本参数：

| Lua 参数 | 实际值 |
|---|---|
| `KEYS[1]` | 稳定的 Session Key |
| `KEYS[2]` | generation Key |
| `ARGV[1]` | 客户端旧 secret 的摘要 |
| `ARGV[2]` | 保存新 secret 摘要的会话 JSON |
| `ARGV[3]` | 剩余 TTL |

脚本步骤：

```text
GET Session
  -> 不存在：返回 0
  -> JSON 中 digest != 旧摘要：返回 0
  -> Session generation != 当前 generation：返回 -1
  -> SET 同一个 Session Key 为新 JSON 和新 TTL
  -> 返回 1
```

两个请求同时拿旧 Token：

```text
请求 A Lua：旧摘要匹配 -> 写入新摘要 A -> 成功
请求 B Lua：读取到新摘要 A -> 与旧摘要不匹配 -> 失败
```

Python 预检查不能单独解决并发，因为两个请求可能同时预检查成功；最终比较和写入必须在 Lua 内
原子完成。

## 18. `rotate_refresh_session()` 完整步骤

```text
1. `_parse_token()` 检查 v1、段数、user_id 和非空字段
2. 根据 user_id/session_id 生成稳定 Session Key
3. Redis GET 会话 JSON
4. JSON 转 RefreshSessionData
5. 解析 expires_at 和 last_activity_at
6. 检查 JSON 中 user_id/session_id 与 Token 一致
7. 检查绝对过期
8. 检查空闲过期
9. 保留 session_id，生成新 secret
10. 组装新 JSON，created_at/expires_at 不变
11. Lua 原子比较旧摘要、generation 并覆盖新摘要
12. 成功返回 `(user_id, new_raw_token)`
```

为什么 `expires_at` 不延长：Refresh 有绝对最长寿命。持续刷新只更新 `last_activity_at`，不能让一次
登录永久存在。

## 19. 绝对过期和空闲过期

| 规则 | 比较方式 | 含义 |
|---|---|---|
| 绝对过期 | `now >= expires_at` | 从首次登录算起到达最大寿命 |
| 空闲过期 | `now - last_activity_at > idle_timeout` | 太久没有执行 Refresh |

满足任意一个都撤销当前 Session 并要求重新登录。

普通 Access 请求不会更新 `last_activity_at`，否则每次 API 都必须访问 Redis，失去 JWT 无状态请求
的优势。

## 20. 单设备退出为什么不验证最新 secret

```python
revoke_refresh_session(redis, raw_token)
```

解析 user_id/session_id 后直接删除稳定 Session Key，并从 Set 中移除 session_id。

假设 Refresh 刚轮换，但浏览器同时发出的退出请求还携带旧 Token：如果退出要求旧 digest 必须匹配
最新版，会删除失败；稳定 session_id 让旧 Token 仍能定位并撤销当前设备会话。

删除操作是幂等的：Key 不存在时 `DEL`/`SREM` 仍可重复执行，不需要把重复退出当错误。

## 21. 全部设备撤销为什么只增加 generation

```text
generation 原来为 0
改密/删号 -> INCR -> 1
```

所有旧 Session JSON 保存 `generation=0`。下一次轮换时：

```text
Session generation 0 != Redis 当前 generation 1
-> 刷新失败
```

Lua 同时删除 `sessions` Set。旧 Session String 不立刻遍历删除，而是等待 TTL 自动清理。

这样做的原因：

- Redis Cluster Lua 不能访问无法提前声明且可能跨分片的大量动态 Key。
- INCR 是原子操作，复杂度固定。
- 即使旧 String 暂时存在，也已经不能恢复登录。

## 22. Router 的 Refresh 流程

```python
refresh_token: Annotated[str | None, Cookie()] = None
```

FastAPI 从名为 `refresh_token` 的 Cookie 读取字符串。流程：

```text
Cookie 缺失 -> 401
  -> rotate_refresh_session()
  -> 无效/过期/重放 -> 401
  -> 得到 user_id 和新 Refresh
  -> 数据库再次确认 User 存在
  -> 签发新 Access JWT
  -> Set-Cookie 写新 Refresh
  -> 返回新 Access
```

为什么还查数据库：Redis 只保存最小 user_id，不能因为 Redis Session 存在就假定数据库用户仍存在。

## 23. 前端 Refresh single-flight

`api.js` 中：

```javascript
let refreshPromise = null;
```

多个 API 同时收到 401 时，不能各自调用 Refresh，因为 Refresh Token 每次只能轮换成功一次。

```text
第一个 401 -> 创建 refreshPromise -> 发 Refresh
第二个 401 -> 发现已有 Promise -> 等同一个结果
Refresh 成功 -> 保存新 Access -> 所有等待请求各自重试
always -> refreshPromise = null
```

JSON 请求和图片上传共用该 Promise，并且重试时重新读取最新 Access Token。

## 24. Redis 异常怎样处理

Redis 连接失败、超时或 Lua 执行错误不会被当成“用户名密码错误”。全局异常处理器将 Redis 异常
转为 503，含义是服务暂时不可用。

这是失败关闭：登录系统无法确认或保存长期会话时，不签发一个无法管理的登录状态。

## 25. 安全查看 Redis 数据

只查看 Key 和 TTL，不输出 Session JSON、摘要或 Cookie：

```text
SCAN 0 MATCH *:refresh:* COUNT 100
TYPE <key>
TTL <key>
SCARD <sessions-key>
```

不要在聊天、日志或截图中执行/展示：

```text
GET 完整 Session JSON
浏览器 Cookie 原文
REDIS_URL 中的用户名和密码
```

## 26. 常见失败分支

| 场景 | 内部结果 | HTTP 结果 |
|---|---|---|
| Token 不是四段或版本错误 | `_parse_token -> None` | 401 |
| Session Key 不存在 | rotate 返回 None | 401 |
| secret 摘要不匹配 | Lua 返回 0 | 401 |
| 旧 Token 重放 | 摘要已更新，Lua 返回 0 | 401 |
| generation 已增加 | Lua 返回 -1 | 401 |
| JSON 损坏 | 删除当前 Session，返回 None | 401 |
| 绝对/空闲过期 | 撤销 Session | 401 |
| Redis 连接失败 | RedisError | 503 |

## 27. 调试顺序

1. 登录时在 `create_refresh_session()` 观察 user_id、session_id、generation，不查看 secret 原文。
2. 用 Redis `SCAN` 确认 Session、Set、generation Key 的 hash tag 相同。
3. Refresh 时在 Lua 调用前确认旧 digest 和新 digest 不同。
4. 连续使用同一个旧 Cookie 调用两次 Refresh，第一次应成功，第二次应 401。
5. 调用退出后确认 Session Key 消失。
6. 调用改密后确认 generation 增加，所有旧 Token 都不能刷新。

本地行为测试集中在 [tests/test_auth.py](../../tests/test_auth.py)。

