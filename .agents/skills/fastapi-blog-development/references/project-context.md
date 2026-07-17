# 项目上下文

## 架构

```text
app/main.py
  → app/routers/{users,posts}.py
    → app/schemas/*.py 和 app/services/deps.py
    → app/services/{users,posts,security}.py
      → app/models/{user,post}.py
        → PostgreSQL
```

- `main.py` 注册 CORS、可选 OpenTelemetry、用户/文章 Router 和分页。
- `get_db()` 通过 `yield` 提供 `SessionLocal`，并在 `finally` 中关闭。
- 响应 Schema 使用 `from_attributes=True` 序列化 ORM 对象。
- 当前 Service 同时承担业务逻辑和数据访问。

## 数据实体

`users`：`id`、唯一 `username`、`email`、`hashed_password`、`profile`、`disabled`。

`posts`：`id`、外键 `author_id`、`slug`、`title`、`summary`、`body`、`published_at`。

当前 ORM 没有定义 `relationship()`；现有迁移也没有为 email 和 slug 增加唯一约束。

## API 列表

| 方法 | 路径 | 认证要求 |
| --- | --- | --- |
| POST | `/users` | 公开 |
| GET | `/users` | 公开 |
| GET | `/users/user` | 公开 |
| PUT | `/users/{username}` | 活跃用户，仅本人 |
| POST | `/token` | 公开表单登录 |
| GET | `/users/me/posts` | 活跃用户 |
| POST | `/posts` | 活跃用户 |
| GET | `/posts` | 公开 |
| GET | `/posts/{slug}` | 公开 |
| PUT | `/posts/{slug}` | 活跃用户，仅作者 |
| DELETE | `/posts/{slug}` | 活跃用户，仅作者 |

## 认证机制

- OAuth2 密码表单提交到 `/token`。
- 密码使用 Passlib `pbkdf2_sha512`。
- JWT 使用 HS256，`sub=username`，默认 30 分钟过期。
- 受保护请求读取 `Authorization: Bearer <token>`。
- 登录会写 Cookie，但当前认证依赖不会读取该 Cookie。

## 运行环境事实

- `.env` 预期连接 localhost:5432 的 `fastapi_blog` 和 `fastapi_blog_test`。
- Compose 当前声明 `POSTGRES_DB=blog`，初始化 SQL 创建 `blog_test`。
- 已存在的 Volume 可能保留另一组数据库，必须检查 Compose 解析结果和实际数据库。
- 当前 PowerShell 执行 Alembic 需要设置项目根目录 `PYTHONPATH`。

## 已知技术债

- `app/db/base.py` 和 `app/db/session.py` 创建了不同 Declarative Base。
- 测试创建 `TestingSessionLocal`，但实际 yield 应用的 `SessionLocal`。
- 测试未覆盖 `get_db`、未隔离事务，也没有稳定使用测试 URI。
- 测试依赖执行顺序和共享数据。
- CORS 忽略配置来源，允许 `*` 与 credentials。
- 更新用户时未检查新 username/email 冲突。
- slug 没有唯一策略。
- 按用户筛选文章时仍返回全站总数。
- 写操作异常没有明确 rollback。
- 时间没有统一使用带时区 UTC。
- 已定义 `PostUpdate`，但更新路由仍使用 `PostCreate`。

这些内容只是项目上下文，不代表可以在其他任务中自动重构。

