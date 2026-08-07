# Redis Refresh Session 与数据库表方案对比

## 1. 两种方案相同的安全模型

无论 Redis 还是数据库，都建议：

- Access Token 使用短期签名 JWT。
- Refresh Token 使用不可预测随机串，通过 HttpOnly Cookie 传输。
- 服务端只保存 Refresh Token 摘要，不保存明文。
- Refresh 每次使用后轮换，旧 Token 立即失效。
- 退出、改密、删用户支持撤销。
- 绝对过期不能被持续刷新无限延长。

真正变化的是“有状态 Refresh Session 保存在哪里”，不是 JWT 算法。

## 2. 结构对比

### 数据库表模式

```text
refresh_sessions
  id
  user_id
  token_hash
  expires_at
  last_activity_at
  revoked
```

每次刷新执行 SQL 查询、更新旧行、插入新行。过期数据需要定时清理，否则表会持续增长。

### Redis 模式

```text
refresh:<token_hash> -> JSON session data, EX=<remaining ttl>
user:<id>:sessions   -> token hash set
```

Key 到期由 Redis 自动删除；轮换用 Lua 在一个原子操作中删除旧 Key并创建新 Key。

## 3. 优缺点

| 维度 | 数据库 refresh_sessions 表 | Redis Refresh Session |
| --- | --- | --- |
| 依赖数量 | 只依赖现有数据库，更简单 | 多一个 Redis 服务和连接池 |
| 读写延迟 | 通常毫秒级，受数据库负载影响 | 内存访问通常更低延迟 |
| 过期清理 | 需要定时 DELETE 和索引维护 | Key TTL 自动过期 |
| 原子轮换 | 数据库事务、唯一约束 | Lua 或 WATCH/MULTI |
| 扩容共享 | 所有实例共享数据库 | 所有实例共享 Redis |
| 持久性 | PostgreSQL 持久性强 | 取决于 AOF/RDB 与部署策略 |
| 故障影响 | 数据库故障通常整个业务不可用 | Redis 故障主要影响登录/刷新等会话操作 |
| 查询审计 | SQL 查询方便，历史记录可保留 | TTL 后消失，不适合长期审计 |
| 运维成本 | 已有数据库即可 | 需要密码、TLS、持久化、监控、备份/高可用 |
| 数据增长 | 必须主动清理过期行 | TTL 自动限制会话 Key |

## 4. Redis 方案的主要优势

1. TTL 与 Refresh Token 生命周期天然对应，不需要数据库清理任务。
2. 登录和刷新高频时不会占用 PostgreSQL连接池。
3. 多实例共享会话状态，未来可以复用 Redis 做 WebSocket Pub/Sub 和限流。
4. 用户全部会话撤销可以通过用户索引快速完成。
5. Redis 故障时已有短期 Access JWT 仍可继续验证，公开内容也不依赖 Redis。

## 5. Redis 方案不是“无成本简化”

对只有一台服务器的小博客，数据库表模式其实更少依赖、更容易备份，也可能更加简单。Redis 方案只有在以下条件下更划算：

- 项目本来就需要 Redis 做评论跨进程广播。
- 计划实现登录/评论限流。
- 需要多个 FastAPI Worker 或多容器共享会话。
- 愿意维护 Redis 持久化、监控与故障恢复。

如果只是为了避免一张表而引入 Redis，并不会真正降低系统复杂度，只是把复杂度从 SQLAlchemy 转移到了基础设施。

## 6. 为什么当前博客选择 Redis

当前博客已经有实时评论，正式多 Worker 部署最终需要 Redis Pub/Sub；上线前也需要登录、注册和评论限流。因此 Redis 不是只服务 Refresh Token 的额外组件，而是可复用的基础设施。

当前实现保持边界：

```text
普通 Access 请求 -> JWT + users 表，不访问 Redis
登录/刷新/退出/改密 -> Redis Refresh Session
业务数据 -> PostgreSQL
长期安全审计 -> 日志平台或独立审计存储，不放会话 Redis
```

## 7. 上线要求

- Redis 7 或兼容版本，生产固定具体镜像版本。
- 配置 ACL/密码，不向公网暴露 6379。
- 跨不可信网络使用 TLS，即 `rediss://`。
- 至少启用 AOF；能接受全员重新登录时也可把会话视为可丢失数据。
- 设置 `maxmemory` 与符合预期的淘汰策略；会话 Redis 不应因缓存挤压随意淘汰。
- 监控连接数、内存、命中率、过期数、延迟、拒绝连接和主从状态。
- Redis 不可用时登录/刷新返回 503，不得绕过会话存储继续签发。
- 生产 Access Token 保持 10～15 分钟，减少无状态 Token 无法即时撤销的窗口。
- 部署迁移后原数据库会话失效，应提前通知用户需要重新登录。

## 8. 推荐部署顺序

```text
1. 部署并验证 Redis PING、密码、持久化和监控
2. 注入 REDIS_URL、REDIS_KEY_PREFIX 等环境变量
3. 发布 Redis Refresh 代码
4. 执行 c56c4d6eeba7 迁移删除旧表
5. 验证登录、刷新轮换、重放失败、退出和改密
6. 观察 Redis 内存、TTL 与 503 日志
```

迁移删除表会使旧会话全部失效，这是预期的一次性兼容变化。
