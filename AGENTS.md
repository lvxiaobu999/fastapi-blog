# AGENTS.md

本文件是 AI 编码 Agent 在本仓库中的工作约定，默认作用于整个仓库；如果子目录存在更深层的 `AGENTS.md`，则以更深层文件为准。

## 项目目标

维护一个结构清晰、便于学习的 FastAPI 博客后端，并逐步引入可靠的工程实践。优先选择显式、易读、可测试的实现，让每条请求链都容易跟踪和解释。

## 技术栈与目录

- Python 3.13，使用 `uv` 管理环境与依赖。
- FastAPI、Pydantic v2、SQLAlchemy 2.x、PostgreSQL 17、Alembic。
- OAuth2 Password Flow、JWT、Passlib。
- Pytest、FastAPI TestClient、Ruff、Black、Pylint、Coverage。
- 可选 OpenTelemetry 链路追踪。

```text
app/main.py       组装应用、中间件、Router 和分页
app/routers/      HTTP 参数、依赖、状态码和权限判断
app/schemas/      Pydantic 请求与响应契约
app/services/     业务逻辑、认证与数据访问
app/models/       SQLAlchemy ORM 模型
app/db/           Engine、Session 工厂和 ORM Base
migrations/       Alembic 配置及迁移版本
tests/            API 与 Service 测试
docs/             学习、运行和维护文档
```

正常请求方向：

```text
客户端 → Router → Schema/Depends → Service → ORM → PostgreSQL
客户端 ← response_model 序列化 ← ORM 结果 ← Service
```

除非功能确实需要，否则不要增加新的架构层。

## 修改代码前

1. 将相关 Router、Schema、Service、Model 和测试作为一条完整请求链阅读。
2. 跨层行为参考 `docs/implementation-and-request-flows.md`。
3. 数据库或迁移操作前阅读 `docs/startup-and-migrations.md`。
4. 保留用户已有的无关修改，不擅自清理或覆盖。
5. 只有配置相关任务才读取 `.env`，不得输出密码、完整数据库 URI、JWT 或 `SECRET_KEY`。

## 实现规范

### 教学型中文注释

- 本项目用于学习。新增或修改代码时，对不直观的配置、框架约定、依赖注入、生命周期、事务边界和安全取舍添加清晰的中文注释。
- 每个新增 Python 模块都应在文件顶部使用中文模块 docstring 说明职责、边界和不会负责的内容；公共类、公共函数和路由端点应使用中文 docstring 说明输入、输出或关键副作用。
- 配置项应逐项说明：它控制什么、为什么采用当前值、改成常见的其他值会产生什么行为或副作用。不要只把参数名翻译成中文。
- 对组合配置给出整体解释。例如 `sessionmaker(...)` 不仅说明它创建 Session 工厂，还应解释 `bind`、`autoflush`、`expire_on_commit` 等关键参数。
- 优先使用紧邻代码的行前注释；公共函数、类或模块的整体职责使用中文 docstring。注释应与代码同步更新。
- 简单赋值、显而易见的流程和 Python 基础语法无需逐行注释，避免注释重复代码或淹没核心逻辑。
- 注释用于解释“为什么”和“会怎样”，不能替代清晰命名、类型标注、测试或用户文档。

### 配置文件注释

- 新增或修改 `.env*`、TOML、INI、YAML 等配置时，用该格式支持的中文注释说明配置块用途、值来源、覆盖优先级和修改后的影响。
- 敏感项只说明生成与注入方式，不在注释、文档、命令输出或示例中填写真实密钥、密码和完整生产数据库地址。
- 第三方工具的样板配置只解释本项目实际关心的选项，避免给每个显而易见的日志格式字段堆砌重复注释。

### Router 与 Schema

- Router 只处理 HTTP 相关工作：参数绑定、依赖、响应模型、状态码、权限判断和预期异常转换。
- 可复用的业务与持久化逻辑放在 `app/services/`。
- 为接口明确声明 `response_model` 和成功状态码。
- 输入和输出字段不同时，使用独立的请求与响应 Schema。
- 使用 Pydantic v2 API；ORM 响应使用 `ConfigDict(from_attributes=True)`。
- 绝不返回 `password`、`hashed_password`、密钥或完整 Token 内部信息。
- Pydantic 校验限制应与数据库字段约束保持一致。
- Schema 变化属于公开 API 契约变化，必须同步更新测试和文档。

### Service 与事务

- Service 接收注入的 `Session`，不得自行创建临时 Session。
- 查询和写操作应保持显式、易测试。
- 写操作必须有清晰事务边界：一个操作集中提交，需要数据库生成值时执行 `refresh()`，已处理的写入异常应回滚。
- 不要用宽泛异常捕获隐藏真实错误。
- 不保留 `print()` 调试输出；长期诊断信息使用结构化日志。

### Model 与 Alembic

- Model、Session、测试和 Alembic 应共用同一个 SQLAlchemy Declarative Base。
- 持久化 Model 的每次变更都需要新建 Alembic Migration。
- 应用迁移前必须检查生成的 `upgrade()` 和 `downgrade()`。
- 即使 Router 已提前检查，涉及并发安全的数据不变量仍应由数据库约束保证。
- 除非用户明确要求，不修改已应用过的迁移文件，而是新建迁移。
- 未经用户明确授权，不执行破坏性 downgrade、DROP 或 `docker compose down -v`。

### 认证与授权

- 密码必须哈希存储，禁止保存或记录明文密码。
- 区分认证（用户是谁）和授权（用户能做什么）。
- 受保护资源的修改必须先认证，再验证所有权或角色。
- 身份缺失或无效返回 `401`，已认证但无权限返回 `403`，资源不存在返回 `404`，参数校验失败返回 `422`。
- 除非明确要求迁移认证方式，否则保持 OAuth2 Bearer Token 契约。
- 如果增加 Cookie 认证，必须同时设计 CSRF 防护和安全 Cookie 属性。

### 配置与数据行为

- 新功能优先使用带时区的 UTC 时间。
- 分页总数查询必须与当前筛选条件一致。
- 环境相关值从 Settings 加载，不硬编码密钥、来源、数据库地址或凭据。
- 生产环境 CORS 必须指定明确来源。

## 数据库约定

预期的本地数据库名称：

```text
fastapi_blog       业务数据库
fastapi_blog_test  测试数据库
```

当前已知不一致：`.env` 指向上述名称，而 `compose.yaml` 和 `docker/postgres/init.sql` 仍声明 `blog` 与 `blog_test`。运行 API、测试或迁移前，必须检查配置和实际数据库并有意识地统一；不得静默切换到另一个数据库。

当前 PowerShell 执行 Alembic 前需要设置：

```powershell
$env:PYTHONPATH = (Get-Location).Path
```

## 测试与验证

- 每次行为变化都应新增或更新测试。
- 覆盖成功路径以及相关的 `400`、`401`、`403`、`404`、`422` 路径。
- 测试应自行准备数据，不依赖执行顺序或历史数据。
- 数据库测试应通过 `app.dependency_overrides` 替换 `get_db`，每个用例完成后回滚或清理。
- 当前测试尚未完全隔离，可能使用业务数据库；不得对需要保留的数据直接运行完整测试。

先执行最小范围验证，确认数据库安全后再扩大：

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run pytest tests/test_target.py::test_name -vv -s
uv run pytest tests/test_target.py -vv
uv run pytest -vv
uv run ruff check .
uv run black --check app migrations
uv run pylint app
```

虽然存在 `mypy.ini`，但项目当前没有安装 MyPy。只有安装并实际运行后才能报告 MyPy 通过。

交付前说明执行了哪些命令、结果如何、哪些检查因何跳过，并检查生成的迁移和 API 契约变化。

## 文档与 Agent 行为

- 启动命令、配置、路由、请求链、数据库或迁移流程变化时同步更新 `docs/`。
- 持续维护 `docs/开发流程.md`：每次功能开发都记录新增、修改或删除了哪些关键文件、每个文件的用途、请求调用链、数据库影响和验证命令。文档只描述已经实现的行为，不把计划写成现状。
- 明确标记破坏性命令，区分当前行为和改进建议。
- 诊断异常时阅读完整 Traceback，尤其是最后一个数据库异常。
- 只完成当前任务所需的最小完整改动，不顺带重构无关技术债。
- 未经明确授权，不修改 `.env`、删除数据、重建 Volume、执行破坏性迁移或轮换密钥。
- 处理本项目功能时，优先使用本地 `$fastapi-blog-development` Skill。
