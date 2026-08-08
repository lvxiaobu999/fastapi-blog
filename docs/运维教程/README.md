# 阿里云上线运维教程

这套教程针对当前 FastAPI Blog：FastAPI/Uvicorn、PostgreSQL、Redis Refresh Session、
本地 `/media` 上传目录、WebSocket 实时评论和 Alembic。建议第一次上线按以下顺序阅读：

1. [01-架构选型与采购流程.md](01-架构选型与采购流程.md)：决定买什么、为什么买。
2. [02-域名备案与网络安全.md](02-域名备案与网络安全.md)：域名、备案、DNS、证书、安全组。
3. [03-Docker生产部署.md](03-Docker生产部署.md)：ECS、Docker、Compose、发布和迁移。
4. [04-Nginx与HTTPS配置.md](04-Nginx与HTTPS配置.md)：HTTP、HTTPS、WebSocket、上传限制。
5. [05-环境变量与密钥.md](05-环境变量与密钥.md)：完整变量表和安全注入方式。
6. [06-上线验收与日常运维.md](06-上线验收与日常运维.md)：检查、备份、监控、升级和回滚。
7. [07-开发与生产运行指令.md](07-开发与生产运行指令.md)：开发、发布、迁移、日志和排错命令速查。

## 推荐结论

当前阶段推荐：

```text
用户 -> 阿里云 DNS -> ECS 公网 IP -> Nginx(HTTPS)
                                -> FastAPI 容器
                                      |-> RDS PostgreSQL（VPC 内网）
                                      |-> Tair/Redis（VPC 内网）
                                      `-> ECS 持久卷中的 app/media
```

- 应用和 Nginx：一台 ECS，使用 Docker Compose。
- 数据库：RDS PostgreSQL，不在 ECS 自建。
- Redis：云数据库 Tair（Redis 兼容版），不在 ECS 自建。
- 图片：首次上线可用 ECS 数据盘挂载；准备多实例前迁移 OSS。
- TLS：Nginx 终止 HTTPS，应用仅监听 Compose 内网端口。
- 日志：容器写 stdout，先用 Docker 日志轮转；稳定后接阿里云 SLS。

这是成本、可靠性和维护复杂度较均衡的业内常见小型生产方案。它不是高可用架构：单台
ECS 故障仍会中断服务。业务增长后再升级到 SLB + 多 ECS/ACK + OSS。

## 上线总流程

```text
确认主体与地域 -> 购买域名/计算/数据库/Redis -> ICP 备案
-> 配置 VPC/安全组/白名单 -> 准备 ECS -> 注入 Secret
-> 构建并启动应用 -> Alembic 升级 -> 配置 Nginx
-> DNS 解析 -> 签发证书 -> HTTPS 验收 -> 监控与备份
```

生产操作前先备份数据库。`alembic upgrade head` 只能执行一次并先审查迁移；禁止在生产
执行 `alembic downgrade`、`docker compose down -v`、DROP 或删除数据盘。
