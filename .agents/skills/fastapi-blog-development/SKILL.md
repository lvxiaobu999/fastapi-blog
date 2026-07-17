---
name: fastapi-blog-development
description: 用于实现、调试、审查和维护当前 FastAPI 博客后端。当任务涉及 Router、Pydantic Schema、SQLAlchemy Model/Service、OAuth2/JWT、权限控制、分页、PostgreSQL、Alembic Migration、Pytest、Docker 启动或 OpenTelemetry 请求链时使用。Use for implementing, debugging, reviewing, and maintaining this repository's FastAPI blog backend and its API, database, authentication, migration, testing, and observability workflows.
---

# FastAPI 博客项目开发

遵循仓库既有请求链，保持代码适合中文学习者阅读。首先完整阅读根目录 `AGENTS.md`，它是本仓库的最高优先级开发约定。

## 按需读取上下文

- 功能开发、数据库、认证或排错任务：读取 [项目上下文](references/project-context.md)。
- 需要实施和验证步骤：读取 [任务工作流](references/workflows.md)。
- 需要了解现有跨层链路：读取 `docs/implementation-and-request-flows.md`。
- 涉及启动或迁移操作：读取 `docs/startup-and-migrations.md`。

## 执行流程

1. 将任务归类为问题诊断、功能开发、Schema 变更、认证变更、测试改造或运维文档。
2. 沿 Router → Schema → Depends → Service → Model → Migration → Test 跟踪受影响链路，不适用的层可以跳过。
3. 修改前使用代码证据或最小复现确认当前行为。
4. 设计能够完整解决问题的最小跨层改动，并评估 API 兼容、授权、事务、迁移和数据丢失风险。
5. 一般按 Model/Migration → Schema → Service → Depends → Router → Test → Docs 实施；适合测试先行时可以调整顺序。
6. 先运行最小范围验证；只有确认数据库目标正确且安全后才扩大验证范围。
7. 交付时说明行为变化、修改文件、验证结果和环境阻碍。

## 保持分层边界

- Router：HTTP 参数、依赖、状态码、权限检查和预期异常转换。
- Schema：输入校验和对外序列化契约。
- Service：使用注入 Session 的可复用业务与持久化操作。
- Model/Migration：数据库结构、约束和变更历史。
- Settings/环境变量：密钥及部署相关配置。
- 禁止返回密码哈希、明文密码、密钥或完整 JWT 内部信息。

## 安全处理数据库

- 检查 `.env`、Compose 解析结果、实际数据库和 Alembic 状态，但不输出凭据。
- 预期数据库是 `fastapi_blog` 与 `fastapi_blog_test`；Compose 当前仍声明旧名称，因此必须验证真实状态。
- PowerShell 运行 Alembic 前设置 `$env:PYTHONPATH = (Get-Location).Path`。
- 持久化 Model 变化必须新建迁移并检查升级与回退逻辑。
- 未经明确授权，禁止删除 Volume、DROP 数据或执行破坏性 downgrade。
- 当前测试数据库尚未完全隔离，不对需要保留的数据直接运行完整测试。

## 实现认证与权限

- 受保护接口使用 `get_current_active_user`。
- 无效身份返回 `401`，权限不足返回 `403`。
- 认证之后、修改资源之前检查所有权。
- 新密码必须哈希，禁止记录明文或哈希值。
- 除非用户明确要求迁移，否则保持 Bearer Token 契约。

## 验证结果

根据改动执行针对性验证：

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run pytest tests/test_target.py::test_name -vv -s
uv run ruff check .
uv run black --check app migrations
```

只有确认测试连接的数据库可以被修改后，才能运行完整测试。迁移任务还应检查生成文件和 `uv run alembic current`，并且只应用到已获授权的本地或测试数据库。

## 按边界诊断错误

- `422`：检查请求 Schema 和 Validator；此时 Router 可能尚未执行。
- `401`：检查 OAuth2 Token 提取、JWT 解码与过期、用户查询和 disabled 状态。
- `403`：检查认证后的身份或资源所有权比较。
- `404`：检查查询参数和查询结果。
- `500` 且停在 `engine.raw_connection()`：阅读 Traceback 最后一段，检查容器健康、端口映射、数据库是否存在、凭据和迁移状态。
- 表不存在：确认应用连接的数据库，再应用正确的 Alembic Revision。

不要把 SQLAlchemy 的第一层调用栈当作根因，应以最后一个 DBAPI 异常为准。

## 完成任务

- 删除临时调试输出，不顺带清理无关代码。
- 启动命令、配置、路由或请求链变化时更新项目文档。
- 明确说明通过和跳过的验证。
- 技术债单独说明，不能借当前任务静默扩大范围。

