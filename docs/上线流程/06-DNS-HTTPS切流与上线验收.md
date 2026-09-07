# DNS、HTTPS 切流与上线验收

> 当前正式架构使用 ECS 本机 Docker PostgreSQL/Redis。本文中出现的 RDS/Tair 备份、白名单或监控
> 描述仅适用于未来托管方案；当前数据库备份对象是 `/opt/fastapi-blog/data/postgres`，Redis 备份对象是
> `/opt/fastapi-blog/data/redis`。

本章把已经在 ECS 本机验收通过的站点开放给真实用户。生产命令默认使用以下包装函数：

```bash
export APP_DOMAIN=www.sanwan.xyz  # 按实际域名修改，并与 compose-prod.env 保持一致
export COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
dc() { docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml "$@"; }
```

切流前必须满足备案、证书、页面合规和
回滚准备；DNS 不是用来测试一个尚未健康的服务。

## 1. 切流前最终闸门

- [ ] 大陆站点 ICP 备案状态允许正式开放。
- [ ] 页面已按实际要求展示 ICP 备案号及链接。
- [ ] 公安联网备案办理计划明确；取得编号后有展示位置。
- [ ] 隐私政策、用户协议/评论规则已根据实际业务完成评估和实现。
- [ ] 正式域名证书链、私钥和到期告警已确认。
- [ ] `curl --resolve` 的 HTTP、HTTPS、live、ready 全部通过。
- [ ] 登录、Refresh、退出、管理员、上传和 WebSocket 已用 hosts 内测。
- [ ] PostgreSQL 数据目录、Redis AOF、media 备份、监控和回滚版本都存在。

任何一项是未知状态，都先停止切流。

## 2. 确认唯一正式域名

当前 Nginx 模板只有一个 `${APP_DOMAIN}`，例如：

```text
blog.example.com
```

`APP_DOMAIN`、`ALLOWED_HOSTS[0]`、SSL 证书和 DNS 记录必须一致。

当前模板没有完整实现“根域 + www + 子域多域名统一跳转”。不要把空格分隔的多个域名硬塞进
`APP_DOMAIN`。需要多个入口时，应单独修改并测试 Nginx server block、证书 SAN、
`ALLOWED_HOSTS` 和 canonical redirect。

## 3. DNS 记录

如果正式地址是 `blog.example.com`：

```text
主机记录: blog
记录类型: A
记录值:   <ECS_PUBLIC_IP 或 EIP>
TTL:      上线初期可使用较短的控制台可选值
```

如果使用根域，主机记录通常是 `@`。不要把 RDS/Tair 内网地址放入公共 DNS。

切换前记录旧值和回滚方法；有旧站点时先降低 TTL，并等待旧 TTL 生效后再切。

## 4. 检查 DNS 传播

Windows PowerShell：

```powershell
Resolve-DnsName <APP_DOMAIN>
```

Linux：

```bash
dig +short <APP_DOMAIN>
```

返回值应为预期公网 IP。不同递归 DNS 有缓存，不能只检查自己电脑一次。

## 5. 开放安全组

确认服务健康后，把 80/443 来源按计划开放。SSH 22 仍只允许管理员来源。

ECS 查看监听：

```bash
sudo ss -lntp
```

宿主机应看到 80/443。FastAPI 8000 不应作为宿主机公网监听；PostgreSQL 5432 和 Redis 6379 也不应
出现在 ECS 公网入方向规则中。

## 6. 公网 HTTP/HTTPS 验收

DNS 生效后：

```bash
curl -I "http://$APP_DOMAIN/"
curl -fsS -o /dev/null -w 'HTTPS status: %{http_code}\n' "https://$APP_DOMAIN/"
curl -fsS "https://$APP_DOMAIN/health/live"
curl -fsS "https://$APP_DOMAIN/health/ready"
```

预期：

- HTTP 301 到 `https://$APP_DOMAIN/...`。
- HTTPS 使用真实 GET 请求返回 200。
- live/ready 分别返回 ok/ready。
- 响应有 `X-Request-ID`。

证书检查：

```bash
openssl s_client -connect "$APP_DOMAIN:443" -servername "$APP_DOMAIN" </dev/null
```

重点检查证书主题/SAN、完整链、有效期和 verify 结果。不要把私钥用于任何在线检查工具。

## 7. 安全响应头

```bash
curl -sI "https://$APP_DOMAIN/"
```

确认至少存在项目当前配置的：

```text
Content-Security-Policy
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
X-Frame-Options: DENY
Permissions-Policy
X-Request-ID
```

HSTS 当前在 Nginx 模板中注释。只有确认所有需要覆盖的域名/子域都永久支持 HTTPS 后再逐步启用；
错误的 `includeSubDomains` 可能让其他尚未支持 HTTPS 的子域长期不可访问。

## 8. Cookie 和认证验收

使用专门生产验收账号，在浏览器开发者工具中检查：

1. 登录返回 Access Token，Refresh Token 不出现在 JSON。
2. Refresh Cookie 有 `Secure`、`HttpOnly`、`SameSite=Lax`。
3. Cookie Path 是 `/api`。
4. 受保护 API 使用 `Authorization: Bearer ...`。
5. Access 失效后只发生一次 Refresh，原请求能够重试。
6. 退出后 Refresh Cookie 删除，旧 Refresh Token 不能恢复会话。
7. 普通用户访问管理员接口返回 403，不能只依赖前端隐藏。

不要在截图、HAR、日志或工单中公开 Cookie/Token 原文。

## 9. 上传持久化验收

1. 上传测试头像。
2. 上传文章横图。
3. 记录 URL，但不公开测试账号凭据。
4. 在正式开放前或维护窗口重建 app 和 Nginx 容器：

   ```bash
   dc up -d --force-recreate app nginx
   ```

5. 再访问图片，确认文件仍存在。
6. 检查宿主 `/opt/fastapi-blog/data/media` 和备份任务。

如果重建后文件丢失，说明写入了容器层或挂载错误，不能继续正式上线。

## 10. WebSocket 验收

打开两个独立浏览器会话访问同一篇已发布文章：

1. 两边都使用 `wss://` 成功建立连接。
2. 登录用户发送评论后，提交者收到成功确认。
3. 另一个浏览器实时看到 `comment.created`。
4. 回复关系和刷新后的历史评论一致。
5. Token 失效、文章下架和限流分支符合预期。

同时查看：

```bash
dc logs --tail=200 nginx app
```

当前只能验证单 app 进程内广播，不能据此证明多实例广播可用。

## 11. 业务功能验收

使用专门验收数据覆盖：

| 功能 | 预期 |
|---|---|
| 首页/分类/搜索/详情 | 公开数据正确，下架文章不泄露 |
| 注册/登录/改密/退出/注销 | 状态码、Cookie、会话撤销正确 |
| 普通用户/管理员 | 401、403 和管理员操作边界正确 |
| 文章创建/编辑/上下架 | 数据提交、图片、公开过滤正确 |
| 评论/回复 | 持久化、实时广播、错误提示正确 |
| 错误页/API 错误 | 不返回 Traceback、URI 或 Secret |
| 日志 | 有 request_id，无密码、Token、Cookie、完整连接 URI |

验收数据应标记并在确认无依赖后通过应用功能清理，不直接操作生产表。

## 12. 限流和错误码验收

Nginx 当前为公共接口和认证接口设置不同速率。只做受控、小规模验证，不在正式环境运行压测：

- 正常页面浏览不会误触 429。
- 登录/Refresh/注册高频请求会得到 429。
- 大于 6 MB 的上传由 Nginx 拒绝，应用业务限制仍是 5 MB。
- app 不健康时 Nginx 可能返回 502，应有告警。
- Redis 故障的认证服务返回安全 503，不泄露连接信息。

## 13. 日志和监控验收

```bash
dc ps
docker stats --no-stream
dc logs --tail=200 app nginx postgres redis
docker system df
```

确认：

- app 没有持续重启。
- 日志是预期 JSON/访问日志，没有 Secret。
- Docker 日志 `max-size=20m`、`max-file=5` 生效。
- ECS、PostgreSQL/Redis 容器和证书告警联系人能收到测试告警。
- 磁盘、内存和连接数有初始基线。

## 14. 上线记录

```text
正式开放时间：
域名/DNS 记录：
ECS 实例与公网 IP：
Git tag/commit：
Docker Image ID：
Alembic current/head：
PostgreSQL 备份位置/时间：
Redis AOF 备份位置/时间：
证书到期时间：
验收账号负责人：
监控告警负责人：
已知残余风险：
```

不要把密码、Secret 或完整 Token 写进上线记录。

## 15. 本阶段完成标准

- [ ] DNS 已指向预期固定公网地址。
- [ ] 80/443 正常，22 仍限制来源，其他内部端口未暴露。
- [ ] 证书、跳转、健康检查和安全头通过。
- [ ] Cookie、Refresh、权限、上传和 WebSocket 通过。
- [ ] 重建 app 后上传文件仍存在。
- [ ] 日志、监控、备份和到期告警已经验证。
- [ ] 上线版本和残余风险已经记录。

上线后继续阅读 [07-日常发布回滚备份与故障处理](./07-日常发布回滚备份与故障处理.md)。
