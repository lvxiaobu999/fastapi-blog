# 生产镜像固定 Python 3.13 系列，避免构建机本地环境影响容器运行结果。
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# 从官方镜像复制固定版本的 uv；升级 uv 时应先在测试环境重新构建并运行完整测试。
COPY --from=ghcr.io/astral-sh/uv:0.11.9 /uv /uvx /bin/

# 先复制依赖清单，业务代码变化时可以继续复用依赖安装缓存。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 应用运行需要模板、静态文件和默认媒体目录，迁移命令需要 migrations 与 alembic.ini。
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini README.md ./
RUN uv sync --frozen --no-dev

# 应用不使用 root 运行。宿主机挂载的 media 目录需要允许 UID 10001 写入。
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/app/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 当前 WebSocket 房间保存在进程内存，一个容器暂时只启动一个应用进程。
CMD ["uv", "run", "fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
