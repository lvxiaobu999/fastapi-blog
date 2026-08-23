# 三碗博客 阿里云上线流程

如果你只是要把个人博客先稳定上线，请先阅读 [个人博客最简上线流程](../个人博客最简上线流程.md)。
它只保留当前代码真正需要的服务器、PostgreSQL、Redis、HTTPS 和媒体持久化；本目录下面的
分阶段 Runbook 适合后续需要阿里云 ECS、RDS、Tair、备案、监控和回滚细节时再使用。

如果域名仍在备案、TLS 证书尚未签发，只需要先用固定公网 IPv4 做受控验收，请先阅读
[公网 IP 临时部署](./00-公网IP临时部署.md)。该流程使用独立的 HTTP-only Compose，不挂载证书，
并通过显式 `PUBLIC_IP_MODE` 让 Refresh Cookie 在 HTTP 下可用；它不能替代正式 HTTPS 上线。

本次阿里云构建、健康检查、DNS 和端口故障的复盘，以及“无域名/备案后域名”两套运行命令，
见 [阿里云部署错误排查](../阿里云部署错误排查/README.md) 和 [公网 IP 与域名运行文档](../阿里云部署错误排查/02-公网IP与域名运行文档.md)。
逐条学习附件命令的检查顺序和副作用，见 [错误指令学习与排查流程](../阿里云部署错误排查/03-错误指令学习与排查流程.md)。

本目录是一份按时间顺序执行的首次上线 Runbook，面向第一次把 Python/FastAPI 项目托管到
阿里云的开发者。它不是只罗列云产品名称，而是说明每一步的前置条件、操作目的、执行命令、
预期结果和停止条件。

文档依据当前仓库中的 `Dockerfile`、`compose.production.yaml`、
`nginx/default.conf.template`、Alembic Migration 和 Settings 编写。云产品可售规格、备案材料、
免费证书额度和控制台入口可能变化，购买前仍应以阿里云控制台、工信部和公安机关的当时要求
为准。

## 1. 当前推荐架构

```text
浏览器
  -> 阿里云 DNS
  -> ECS 公网 IP / EIP
  -> Nginx 容器（80/443、TLS、限流、WebSocket Upgrade）
  -> FastAPI 容器（只在 Compose 内网暴露 8000）
       |-> RDS PostgreSQL（VPC 内网）
       |-> Tair/Redis（VPC 内网）
       `-> ECS /opt/fastapi-blog/data/media 持久目录
```

当前选择的边界：

| 组件 | 部署位置 | 原因 |
|---|---|---|
| FastAPI、Nginx | 一台 ECS 的 Docker Compose | 对新手可理解、可回滚，成本可控 |
| PostgreSQL | RDS PostgreSQL | 备份、监控、补丁和存储不由应用容器承担 |
| Refresh Session | Tair Redis 兼容版 | 当前登录续期依赖 Redis、TTL 和 Lua |
| 用户上传 | ECS 持久目录 | 当前代码直接读写 `app/media`，首次上线改动最小 |
| HTTPS | Nginx 终止 TLS | FastAPI 只接收 Compose 内网 HTTP |
| 日志 | 容器 stdout + Docker 轮转 | 当前生产默认 JSON，后续可接 SLS |

这不是高可用架构。一台 ECS 故障会中断站点；当前 WebSocket 房间也只存在单个 Python 进程
内存中，所以不能直接增加多个 worker 或 app 副本。多实例前必须先把上传迁移到 OSS，并给
WebSocket 增加 Redis Pub/Sub 等跨实例广播。

## 2. 阅读和执行顺序

临时 IP 验收：先读 [00-公网 IP 临时部署](./00-公网IP临时部署.md)，完成后再回到正式流程。

1. [01-上线方案与采购备案](./01-上线方案与采购备案.md)
2. [02-云资源与网络初始化](./02-云资源与网络初始化.md)
3. [03-发布前检查与Docker打包](./03-发布前检查与Docker打包.md)
4. [04-ECS初始化与生产配置](./04-ECS初始化与生产配置.md)
5. [05-首次部署与数据库迁移](./05-首次部署与数据库迁移.md)
6. [06-DNS-HTTPS切流与上线验收](./06-DNS-HTTPS切流与上线验收.md)
7. [07-日常发布回滚备份与故障处理](./07-日常发布回滚备份与故障处理.md)
8. [08-上线总检查表](./08-上线总检查表.md)

已有 [运维教程](../运维教程/README.md) 继续作为专题参考；本目录负责告诉你“此刻下一步做
什么”，运维教程负责解释某个主题的更多背景和命令。

## 3. 从采购到上线的总链路

```text
确定主体、地域和域名
  -> 购买域名、ECS、RDS、Tair、公网带宽/地址
  -> 域名实名认证，准备大陆 ICP 备案
  -> 建立同地域 VPC、交换机、安全组和内网白名单
  -> 初始化 RDS 数据库账号、Tair 账号、备份和告警
  -> 本地测试、审查迁移、确定 Git tag/commit
  -> Dockerfile 构建不可变应用镜像
  -> 初始化 ECS、部署目录、Secret、证书和媒体目录
  -> 一次性执行 Alembic upgrade head
  -> 启动 FastAPI + Nginx Compose
  -> 使用 hosts/curl --resolve 做切流前验收
  -> DNS 指向 ECS，验证 HTTPS、登录、上传和 wss 评论
  -> 创建生产管理员、开启监控备份、记录上线版本
```

## 4. 每个阶段的停止条件

不要带着已知错误继续下一阶段：

| 阶段 | 只有满足下列条件才能继续 |
|---|---|
| 采购 | 地域、主体、域名、预算、续费责任人已确认 |
| 网络 | ECS 能通过内网访问 RDS/Tair，公网没有开放 5432/6379/8000 |
| 打包 | 测试/静态检查通过，迁移已审查，版本有明确 tag/commit |
| 配置 | Settings 校验通过，Secret/证书不在 Git 或镜像中 |
| 迁移 | RDS 备份完成，目标数据库和待执行 Revision 已二次确认 |
| 启动 | app 健康，Nginx `nginx -t` 通过，日志无 Secret |
| 切流 | ICP/证书/备案展示等合规前置满足，域名前置验收通过 |
| 完成 | 功能、备份、监控、回滚入口和负责人全部验收 |

## 5. 占位符规则

文档命令中的下列内容都必须替换，不能原样执行：

```text
<APP_DOMAIN>          例如 blog.example.com
<ECS_PUBLIC_IP>       ECS 的固定公网地址或 EIP
<RDS_PRIVATE_HOST>    RDS VPC 内网连接地址
<TAIR_PRIVATE_HOST>   Tair VPC 内网连接地址
<RELEASE_TAG>         已测试的 Git tag，例如 v0.1.0
<ADMIN_IP>            管理员固定公网 IP
```

真实数据库密码、Redis 密码、JWT Secret、Cookie、Token 和 TLS 私钥不应出现在命令历史、
Git、镜像、日志、截图、聊天或本文档中。

## 6. 高风险命令统一约定

下列操作不会作为日常上线命令使用：

- `docker compose down -v`：可能删除持久卷。
- `alembic downgrade ...`：Migration 的回退可能删表、删字段或丢数据。
- `alembic stamp ...`：只改版本记录，不执行数据库结构 SQL。
- `DROP DATABASE/TABLE`、清空 RDS、删除数据盘或快照。
- `docker system prune -a --volumes`：会删除回滚镜像和卷。
- 在生产 ECS 运行 Pytest、生成 Migration 或直接修改源码。

生产数据库变更前必须先完成可恢复备份，并人工检查目标实例、数据库名、当前 Revision 和
Migration 内容。

## 7. 当前仓库的上线前缺口

以下内容不能只靠云资源配置解决：

1. `app/templates/layout.html` 当前页脚没有 ICP 备案号和公安联网备案号展示。
2. 当前没有独立的隐私政策、用户协议或服务条款页面；站点允许注册、上传和评论，正式运营前
   应根据实际收集的信息、主体和监管要求完成合规评估并补齐。
3. `/media` 仍是单机目录，没有 OSS、多机同步或异地自动恢复。
4. WebSocket 广播仅在单进程内有效，不能直接横向扩容。
5. 当前 Compose 要求启动前已经有 `fullchain.pem` 和 `privkey.pem`，没有内置 Certbot 首次
   签发流程；第一次建议使用阿里云 SSL 证书的 DNS 验证并提前下载证书。

这些是上线检查项，不应在文档中伪装成已经实现。
