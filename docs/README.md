# 博客项目文档

这里是三磗博客的文档总入口。文档按使用场景分为项目开发、功能专题、部署运维和 AI 四个区域；先阅读本页，再进入对应专题即可。

## 快速开始

在项目根目录执行：

```powershell
uv sync
docker compose -f compose.redis.development.yaml up -d
uv run alembic upgrade head
uv run fastapi dev app/main.py
```

打开 <http://127.0.0.1:8000>。存活检查为 `/health/live`，就绪检查为 `/health/ready`，接口文档为 `/docs`。页面测试会自动替换为 SQLite 和测试 Redis。

## 按目标阅读

### 项目开发

- [开发流程](开发流程.md)：当前分层、请求链、事务边界、开发步骤和验证命令。
- [环境配置](01-项目与开发/环境配置.md)：Settings、环境变量和敏感配置注入。
- [同步与异步开发指南](01-项目与开发/同步与异步开发指南.md)：异步请求链和阻塞代码处理方式。
- [Router 入口与请求调用链](01-项目与开发/Router入口与请求调用链详解.md)：页面/API Router 的参数、依赖和响应模型。
- [Service 职责与调用链](01-项目与开发/Service职责与调用链详解.md)：业务规则、查询和事务提交。
- [核心代码流程](01-项目与开发/核心代码流程/README.md)：JWT、Redis、Logging、WebSocket 四条完整链路。
- [数据库设计](01-项目与开发/数据库设计.md)：ORM 模型和数据关系。
- [数据库迁移与常用操作](01-项目与开发/数据库迁移与常用操作.md)：Alembic 迁移、检查与回滚边界。
- [用户接口](01-项目与开发/用户接口.md)：用户、认证和文章接口说明；实时契约以 `/docs` 为准。
- [代码审查报告（2026-09-13）](01-项目与开发/代码审查报告-2026-09-13.md)：最近一次全项目审查结论和后续建议。

### 功能专题

- [认证与会话](02-功能专题/认证与会话)：JWT、Refresh Session、QQ OAuth、密码找回和管理员调试。
- [评论与实时通信](02-功能专题/评论与实时通信)：评论模型、WebSocket 协议和前端交互。
- [日志与可观测性](02-功能专题/日志与可观测性)：Python logging、请求追踪和日志文件。
- [存储与基础设施](02-功能专题/存储与基础设施)：Redis 启动和连接实践。

### 部署运维

- [上线流程](03-部署运维/上线流程/README.md)：从资源准备到 HTTPS、验收、回滚和备份的完整 Runbook。
- [运维教程](03-部署运维/运维教程/README.md)：架构、Docker、Nginx、密钥和日常运维。
- [故障排查](03-部署运维/故障排查/README.md)：公网访问、域名和常见错误命令排查。
- [生产环境执行命令流程](03-部署运维/生产环境执行命令流程.md)：生产环境常用命令入口。

### AI

- [AI 专题](04-AI/README.md)：博客 AI 功能边界、知识体系、学习路线和项目实施清单。

## 代码入口速查

| 层 | 目录 | 责任 |
| --- | --- | --- |
| 应用组装 | `app/main.py` | 生命周期、中间件、健康检查和 Router 注册 |
| 页面/API | `app/routers/` | 参数、依赖、权限、状态码和响应模型 |
| 契约 | `app/schemas/` | Pydantic 输入校验与输出序列化 |
| 业务 | `app/services/` | 查询、业务规则、认证和事务提交 |
| 持久化 | `app/models/`、`app/db/` | ORM、AsyncSession、Engine 和 Redis 客户端 |
| 数据库变更 | `migrations/versions/` | Alembic 升级与回退脚本 |
| 浏览器交互 | `app/templates/`、`app/static/js/` | HTML、AJAX、认证状态和 WebSocket |
| 验证 | `tests/` | API、Service、配置、日志和异常路径 |

## 验证命令

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run pytest -q
uv run ruff check .
uv run black --check app migrations   # 已安装 Black 时执行
uv run pylint app                    # 已安装 Pylint 时执行
```

执行迁移前确认 `ENV`、数据库名称、当前 revision、备份和目标主机。生产环境不要运行测试、`alembic downgrade`、`docker compose down -v`、DROP 或删除数据目录。

## 文档维护约定

- 当前行为和开发规范集中写在 [开发流程](开发流程.md)，部署命令集中写在 [上线流程](03-部署运维/上线流程/README.md) 和 [运维教程](03-部署运维/运维教程/README.md)。
- 新功能记录改动文件、请求链、数据库影响和验证命令，避免在多份日期文档中复制同一段命令。
- 历史记录保留原文件并标明背景；正式命令以当前 Compose、Settings 和 Alembic 为准。
- 示例只使用占位符，不写入真实密码、JWT、Cookie、数据库 URI、OAuth Secret 或 TLS 私钥。
