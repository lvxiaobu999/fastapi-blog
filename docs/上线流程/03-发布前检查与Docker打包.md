# 发布前检查与 Docker 打包

本章在开发机或 CI 执行。目标是产生一个可追溯、已经验证、不会携带 Secret 和本地数据的发布
版本。生产 ECS 不用来修代码、生成 Migration 或运行完整测试。

## 1. 先区分三个版本

| 版本 | 例子 | 管理什么 |
|---|---|---|
| Git 版本 | `v0.1.0` / commit SHA | Python、模板、JS、Dockerfile、Migration 源码 |
| Docker 镜像 | `fastapi-blog:abc1234` | 实际运行的 Python、依赖和应用文件 |
| Alembic Revision | `20260810_01` | 生产数据库已经执行到的结构/数据迁移 |

上线记录必须同时写下三者。旧镜像不一定兼容新数据库；数据库升级也不会因为 Git 回退而自动
回退。

## 2. 确认发布范围

在项目根目录执行：

```powershell
git status --short
git diff --check
git diff --stat
git log -1 --oneline
```

逐条含义：

| 命令 | 目的 |
|---|---|
| `git status --short` | 查看已修改和未跟踪文件，防止漏提交 |
| `git diff --check` | 检查冲突标记和行尾空白错误 |
| `git diff --stat` | 确认改动文件范围符合本次发布 |
| `git log -1 --oneline` | 记录当前提交摘要 |

发布版本应来自已经审查的提交或 tag。不要把开发机的脏工作树直接压缩上传生产，也不要为了
“变干净”使用 `git reset --hard` 丢弃未确认的修改。

## 3. 依赖可复现性

```powershell
uv sync --frozen
uv lock --check
```

- `uv sync --frozen` 只按照 `uv.lock` 同步，不在此时偷偷更新依赖。
- `uv lock --check` 检查 `pyproject.toml` 与锁文件是否一致。

如果锁文件过期，应在独立开发改动中更新、测试和审查，而不是上线当天临时放宽 `--frozen`。

## 4. 代码质量检查

```powershell
uv run ruff check .
uv run ruff format --check app migrations tests
```

`ruff check` 检查常见代码错误和规范；`ruff format --check` 只检查格式，不自动改生产候选代码。

项目没有安装 MyPy，所以不能写“类型检查已通过”。只有安装并真实执行后才能记录该结果。

## 5. 测试数据库安全门

当前仓库约定要求先确认测试数据库隔离。只有测试通过 `app.dependency_overrides` 使用可丢弃数据库，
或者你明确确认目标是一次性测试库时，才执行：

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run pytest -q
```

禁止为了发布验证让 Pytest 连接需要保留数据的开发库或生产 RDS。测试应覆盖认证、Refresh、
权限、文章、评论、WebSocket、健康检查和异常响应；失败时修复后重新产生发布候选。

## 6. Alembic 发布检查

先在安全的开发/临时数据库执行只读检查：

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run alembic heads
uv run alembic history
uv run alembic current
uv run alembic check
```

检查重点：

- `heads` 应只有一个 head。
- `current` 是当前验证数据库的版本，不代表生产 RDS。
- `check` 应报告没有遗漏的 Model 差异。
- 每个待发布 Migration 的 `upgrade()` 和 `downgrade()` 都已人工阅读。
- 新增非空字段、唯一约束、外键或数据回填已经考虑历史数据。
- 没有修改已经在其他环境执行过的旧 Migration。

生产发布只执行已有的 `upgrade head`。生产服务器不能执行 `revision --autogenerate`。

## 7. 生成发布标识

提交并审查完成后打 tag，例如：

```powershell
git tag -a v0.1.0 -m "fastapi blog first production release"
git push origin v0.1.0
git rev-parse --short v0.1.0
```

团队也可以只使用完整 commit SHA，但不要使用含义会变化的 `latest` 作为唯一上线记录。

## 8. Dockerfile 当前打包逻辑

仓库使用 [Dockerfile](../../Dockerfile)：

```text
python:3.13-slim
  -> 从固定 ghcr.io/astral-sh/uv 版本复制 uv
  -> 先复制 pyproject.toml/uv.lock 安装生产依赖
  -> 复制 app、migrations、alembic.ini、README.md
  -> 创建 UID 10001 的非 root appuser
  -> fastapi run app/main.py，单进程监听 8000
```

镜像需要包含：

- `app/`：后端、模板、静态文件和媒体目录骨架。
- `migrations/`、`alembic.ini`：发布时执行 Alembic。
- `pyproject.toml`、`uv.lock`：固定运行依赖。

镜像明确不包含：

- `.env*`、生产 Secret 和 TLS 私钥。
- 本地 SQLite、备份、日志和真实上传文件。
- `.git`、`.venv`、测试缓存。
- `docs/` 和 `tests/`。

排除规则由 [.dockerignore](../../.dockerignore) 控制。

## 9. 本地构建镜像

确保 Docker Desktop/Engine 已启动：

```powershell
$imageTag = git rev-parse --short HEAD
docker build --pull --tag "fastapi-blog:$imageTag" .
docker image inspect "fastapi-blog:$imageTag" --format '{{.Id}} {{.Created}}'
docker history "fastapi-blog:$imageTag"
```

`--pull` 尝试取得基础镜像对应标签的最新内容。若要求完全可复现，应进一步把 Dockerfile 基础
镜像固定到已验证 digest；当前仓库尚未这样做，因此要把基础镜像变化视为残余供应链风险。

检查敏感文件没有进入镜像：

```powershell
docker run --rm --entrypoint sh "fastapi-blog:$imageTag" -c `
  'test ! -e /app/.env && test ! -e /app/.env.production && test ! -e /app/fastapi_blog.db'
```

命令没有输出且退出码为 0 表示这些路径不存在。它不能证明镜像绝对没有 Secret，还要结合
`.dockerignore`、`docker history` 和镜像扫描。

## 10. 容器内容冒烟检查

不连接数据库也可以检查关键文件：

```powershell
docker run --rm --entrypoint sh "fastapi-blog:$imageTag" -c `
  'test -f /app/app/main.py && test -f /app/alembic.ini && test -d /app/migrations/versions'
```

应用真正启动会立即校验生产 Settings，并在 readiness 中连接 RDS/Tair，所以完整容器冒烟需要
一套隔离的 PostgreSQL/Redis 或 staging 环境，不能拿生产凭据在开发机随意测试。

### 10.1 静态文件的发布边界

`app/static/`（CSS、JavaScript、Logo、favicon 和 vendor 文件）是代码发布物的一部分。Dockerfile
通过 `COPY app ./app` 将它们写入 `fastapi-blog:<commit>` 镜像；生产 Compose 只把用户上传的
`app/media` 挂载到宿主机。不要再创建或配置 `/opt/fastapi-blog/data/static`，也不要把它挂载到
`/app/app/static`，否则宿主机旧目录会覆盖镜像中新版本的静态文件，造成“代码已更新但样式/图片仍旧或 404”。

发布前可在不启动正式服务的情况下检查静态文件确实进入镜像：

```bash
docker compose --env-file "$COMPOSE_ENV" -f "$COMPOSE_FILE" run --rm --no-deps app sh -c \
  'test -f /app/app/static/css/site.css && \
   test -f /app/app/static/js/api.js && \
   test -f /app/app/static/images/logo.png && \
   test -f /app/app/static/images/favicon.ico'
```

若该检查失败，说明构建上下文、提交版本或 Dockerfile 不正确；不要通过手工复制到 `data/static`
掩盖问题，应先修复镜像构建。

## 11. Compose 语法预检

当前生产编排文件是 [compose.production.yaml](../../compose.production.yaml)。它需要两个层次
的变量：

```text
Compose 变量：APP_DOMAIN、APP_IMAGE_TAG、APP_ENV_FILE、MEDIA_HOST_PATH、TLS_HOST_PATH
应用变量：APP_ENV_FILE 中的 ENV、DATABASE_URL、REDIS_URL、SECRET_KEY 等
```

在本地只做语法检查时，可使用不含真实 Secret 的有效测试环境文件：

```powershell
$env:APP_DOMAIN = "blog.example.com"
$env:APP_IMAGE_TAG = $imageTag
$env:APP_ENV_FILE = (Resolve-Path .env.production).Path
$env:MEDIA_HOST_PATH = "D:/tmp/fastapi-blog/media"
$env:TLS_HOST_PATH = "D:/tmp/fastapi-blog/tls"
docker compose -f compose.production.yaml config -q
```

`config -q` 只验证 Compose 解析，不证明目录、证书、RDS/Tair 或应用 Settings 可用。不要执行
不带 `-q` 的 `config` 后把展开结果发到外部，因为展开后的 `env_file` 相关信息可能涉及环境。

## 12. 源码怎样到 ECS

当前 Compose 同时声明 `build:` 和本地镜像名，最直接的第一次上线方式是：

```text
Git 仓库保存已测试 tag
  -> ECS 用只读 Deploy Key 拉取该 tag
  -> ECS 根据同一 Dockerfile 本地 build
  -> 镜像标签使用 commit 短 SHA
```

优点是无需先改 Compose 接入镜像仓库；缺点是 ECS 需要构建资源和访问基础镜像仓库。

不要把个人 Git 私钥复制到 ECS。私有仓库使用只读 Deploy Key 或受控发布包。使用发布包时，
`git archive` 只包含已提交文件，比直接压缩工作目录更可靠：

```powershell
git archive --format=zip --output="fastapi-blog-v0.1.0.zip" v0.1.0
```

阿里云 ACR 是后续更标准的方式，但当前 `compose.production.yaml` 的 `image:` 还没有仓库地址
变量。接入 ACR 前应先修改 Compose、在测试环境验证登录/拉取/回滚，再把它作为已实现流程；
本文不假设 ACR 已接入。

## 13. 发布清单

记录：

```text
发布 tag/commit：
镜像标签和 Image ID：
Alembic head：
测试结果：
Ruff 结果：
迁移审查人：
计划上线时间：
计划回滚版本：
```

## 14. 本阶段完成标准

- [ ] Git 发布范围已审查，版本有明确 tag/commit。
- [ ] 依赖锁、Ruff 和格式检查通过。
- [ ] 只在确认隔离后运行 Pytest，并记录结果。
- [ ] Alembic 只有一个 head，待执行 Migration 已人工审查。
- [ ] Docker 镜像构建成功并记录 Image ID。
- [ ] 镜像不包含 `.env*`、SQLite、上传文件和私钥。
- [ ] Compose 语法检查通过。
- [ ] 已确定用 Git tag 在 ECS 构建，不把脏工作树上传生产。

下一步进入 [04-ECS初始化与生产配置](./04-ECS初始化与生产配置.md)。
