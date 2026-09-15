# 三磗博客

一个用于学习和实践的 FastAPI 个人博客，采用 FastAPI、Pydantic v2、SQLAlchemy 2 异步访问、PostgreSQL、Redis Refresh Session、JWT、Alembic 和 WebSocket 实时评论。

## 快速启动

```powershell
uv sync
docker compose -f compose.redis.development.yaml up -d
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

打开 <http://127.0.0.1:8000>；健康检查为 `/health/live` 和 `/health/ready`，Swagger 为 `/docs`。

## 主要功能

- 文章、分类、摘要、上下架和 Markdown 编辑器
- 用户注册、Bearer JWT、Redis Refresh Session 和邮箱验证码找回密码
- 可选 QQ OAuth 登录、头像上传和管理员后台
- 点赞、收藏、浏览足迹、评论回复和 WebSocket 实时广播
- 统一 API 响应、异常页面、请求 ID、结构化日志和 Docker/Nginx 部署

## 代码结构

```text
app/main.py       应用组装、生命周期、中间件和健康检查
app/routers/      页面/API 参数、依赖、权限和状态码
app/schemas/      Pydantic 输入/输出契约
app/services/     业务规则、数据访问、认证和文件处理
app/models/       SQLAlchemy ORM 与约束
app/db/           AsyncSession、Engine 和 Redis 客户端
migrations/       Alembic 迁移
tests/            API、Service、配置、日志和异常测试
docs/             按开发、功能、部署运维、AI 分类的文档
```

## 文档入口

完整文档从 [docs/README.md](docs/README.md) 开始；开发规范和请求链见 [docs/开发流程.md](docs/开发流程.md)，代码审查结论见 [docs/01-项目与开发/代码审查报告-2026-09-13.md](docs/01-项目与开发/代码审查报告-2026-09-13.md)。

## 验证

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run pytest -q
uv run ruff check .
```

Black 和 Pylint 需在环境中安装后执行。数据库迁移前确认 `ENV` 和目标数据库；生产环境不要执行测试、`alembic downgrade`、`docker compose down -v` 或 DROP 操作。
