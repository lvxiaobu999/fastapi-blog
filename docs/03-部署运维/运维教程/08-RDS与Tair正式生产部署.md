# RDS 与 Tair 方案说明（历史参考）

> 当前项目正式环境已经改为单机 Docker 方案：PostgreSQL 和 Redis 直接运行在 ECS 的 Docker
> Compose 中。本文件不再是当前启动流程，请阅读[《Docker 本机 PostgreSQL 与 Redis 正式部署教程》](08-Docker本机PostgreSQL与Redis正式部署.md)。

## 为什么保留这份说明

RDS PostgreSQL 和 Tair Redis 是云厂商托管服务，可以提供自动备份、监控和更高可用性，但需要额外
购买资源。此前的生产文档按该方案编写，现因成本考虑切换为自建容器。

## 当前与历史配置的区别

| 项目 | 当前单机 Docker（使用） | 历史 RDS/Tair（不使用） |
|---|---|---|
| PostgreSQL 主机 | \`postgres\`（Compose 服务名） | RDS VPC 私网地址 |
| Redis 主机 | \`redis\`（Compose 服务名） | Tair VPC 私网地址 |
| 启动方式 | \`docker compose up -d\` 创建四个容器 | 阿里云控制台管理托管实例，Compose 只创建 app/nginx |
| 数据持久化 | ECS \`PG_DATA_PATH\`、\`REDIS_DATA_PATH\` | 云服务自动备份/存储 |
| 维护责任 | 你负责备份、升级、恢复和安全 | 云服务商承担部分基础设施维护 |
| 生产配置 | \`app.env\` + \`compose-prod.env\` | app.env 只填托管服务连接串 |

## 迁移回托管服务时的原则

如果未来需要 RDS/Tair，请先完成 PostgreSQL 数据迁移、Redis 会话影响评估和回滚演练，然后：

1. 停止本机 app 流量并备份本机数据目录。
2. 在 RDS/Tair 创建数据库、账号、白名单和备份策略。
3. 将 \`app.env\` 中的 \`DATABASE_URL\`、\`REDIS_URL\` 改为托管服务地址。
4. 修改 Compose，移除本机 \`postgres\`、\`redis\` 服务及其依赖。
5. 在新目标执行只读连接检查和 Alembic 迁移，再恢复流量。

不能让本机 PostgreSQL 与 RDS 同时接收写入，也不能只改连接串而不迁移已有数据。
