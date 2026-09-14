# 阿里云部署错误排查与命令复盘

> 当前正式部署是 ECS 单机 Docker Compose 四容器：`app`、`nginx`、`postgres`、`redis`。
> 本目录中的 RDS/Tair 内容是旧故障记录或未来迁移参考，不是当前生产启动方式；当前连接串由
> `/opt/fastapi-blog/config/compose-prod.env` 和 `compose.production.yaml` 自动生成。

本文根据 `启动项目报错-1.txt` 的脱敏记录，以及当前仓库的 `Dockerfile`、Compose、Nginx 和
健康接口整理。附件里的文字是故障记录，不是本项目必须照抄执行的指令；其中包含删除目录、
输出 Secret、覆盖 Docker 配置和硬编码数据库密码等高风险操作，本文将它们标记为“曾尝试”或
“不建议重复”。

## 1. 这次故障不是一个问题

故障链可以还原为：

```text
生产配置缺少必要变量/证书
  -> 误用正式 HTTPS Compose 访问公网 IP
  -> Nginx 证书或 Cookie 配置不匹配
  -> Docker 构建阶段 uv 无法解析 Python 包站点
  -> app 启动后 readiness 访问数据库/Redis 失败
  -> Nginx 因 app 不健康没有启动
  -> 即使依赖恢复，数据库未迁移时首页仍返回 500
```

最终真正阻塞服务的根因是 PostgreSQL 和 Redis 没有部署/监听，连接目标返回
`Connection refused`；`/health/ready` 只是把这个依赖故障准确报告成了 503。

## 2. 问题、证据和正确处理

| 问题 | 证据/现象 | 正确处理 |
|---|---|---|
| 配置缺少 `ALLOWED_HOSTS`、`SECRET_KEY`、入口地址 | Settings 启动校验失败 | 在服务器受限的 `app.env` 中补齐，权限设为 `640`；不把 Secret 写入 Git 或日志 |
| 没有证书却启动 `compose.production.yaml` | Nginx 引用 `fullchain.pem`/`privkey.key` | 无域名/无证书使用 `compose.public-ip.yaml`；域名和证书齐全后才用正式 Compose |
| HTTP + `AUTH_COOKIE_SECURE=true` | 页面能打开，但浏览器不会通过 HTTP 发送 Refresh Cookie | 仅临时公网 IP 模式使用 `PUBLIC_IP_MODE=true` 和 `AUTH_COOKIE_SECURE=false`；切 HTTPS 前恢复 `true` 并轮换密钥/会话 |
| Docker 构建时 `uv sync` 报 DNS error | 容器无法解析 `files.pythonhosted.org`，宿主机 `curl` 正常 | 先区分宿主机 DNS、Docker daemon DNS 和包索引问题；必要时为 Docker daemon 配置可达 DNS，或通过受控 `UV_INDEX_URL` 构建参数使用镜像 |
| 手工请求 readiness 得到 Invalid Host | `curl http://localhost:8000/health/ready` 的 Host 是 localhost，但生产白名单只有公网 IP | 诊断请求必须带 `Host: <ALLOWED_HOSTS[0]>`；仓库 Compose healthcheck 已按白名单第一个值设置 Host |
| readiness 持续 503 | 响应为 `Service dependencies are unavailable`，底层连接被拒绝 | 检查 PostgreSQL/Redis 是否运行、监听和放行；不要通过重启循环掩盖依赖故障 |
| 首页 500 | 日志确认数据库表尚未创建 | 备份并确认目标数据库后执行 `uv run alembic upgrade head` |
| Nginx 80 端口冲突 | 宿主机独立 Nginx 已监听 80 | 先用 `ss`、`systemctl status nginx` 确认进程，再停止已确认的服务；不要凭 PID 直接 `kill` |

## 3. 附件中命令实际改动了什么

以下是日志里出现的命令及其影响。路径和凭据不在本文复现。

| 日志命令/操作 | 影响 | 评价 |
|---|---|---|
| `rm -rf /home/admin/fastapi-blog` | 删除一个与当前 `/opt/fastapi-blog/current` 不同的目录 | 与启动故障无关且不可逆；不要重复，除非先解析绝对路径并获得明确授权 |
| `cat /opt/.../app.env`、`cat .env.production` | 把数据库 URI、Redis URI、Secret 直接输出到终端 | 会泄露凭据；改用 `grep -E '^(ENV|ALLOWED_HOSTS|...)='`，并对 Secret 只检查“是否存在/长度” |
| `cat >> app.env`、`echo >> app.env`、`sudo bash -c ...` | 尝试追加公网 IP、`APP_DOMAIN`、`SECRET_KEY` 等配置；前两次因 heredoc/权限没有生效，后续使用 sudo 写入 | 配置文件确实会被修改；应使用 `sudoedit` 或受限文件管理，避免重复追加和命令历史泄露 |
| `APP_DOMAIN=<IP> docker compose ...` | 只为当前命令临时设置 Compose 变量，未必写回文件 | 可用于一次预检，但每次启动都依赖 shell；应使用不含 Secret 的 Compose 参数文件 |
| `sed -i ... Dockerfile`、重写 Dockerfile | 尝试加入 `UV_INDEX_URL`；第一次插入破坏了 `ENV` 续行，后续重写恢复了语法但仍把镜像源写死 | 当前仓库已改为可选 `UV_INDEX_URL` build arg；默认 Dockerfile 不绑定某个地区镜像 |
| 覆盖 `/etc/docker/daemon.json` 并 `systemctl restart docker` | 添加公共 DNS 和 registry mirror；重启会影响宿主机所有 Docker 容器 | 不能整文件覆盖，必须备份、合并 JSON、验证后重启；公共 DNS 要符合 ECS 网络策略，不能盲抄 `8.8.8.8` |
| `docker run ... nslookup`、宿主机 `curl` | 对比容器和宿主机 DNS/HTTPS 能力 | 这是正确的诊断方向；应分别记录“解析失败”和“TCP/HTTPS 失败” |
| 覆盖 `compose.production.yaml` 添加 `postgres`、`redis` | 把正式 Compose 从外部 RDS/Tair 改成单机自建依赖，并增加 `depends_on` | 当前已直接写入正式 Compose；密码放在 `compose-prod.env`，不硬编码 YAML |
| 把 `DATABASE_URL`/`REDIS_URL` 的主机改成 `postgres`/`redis` | 让 app 使用 Compose 服务名进行内部 DNS 解析 | 当前正式方案固定使用；未来迁移 RDS/Tair 时才改回托管地址 |
| `systemctl stop/disable nginx`、手动 `kill PID` | 释放宿主机 80 端口给 Nginx 容器 | `stop` 前要确认服务归属；`disable` 会改变重启后的系统行为；不要直接 kill 未确认的 PID |
| `docker compose ... exec app uv run --no-sync alembic upgrade head` | 修改目标 PostgreSQL 数据库结构和 `alembic_version` | 这是唯一明确的数据库写操作；执行前必须确认数据库、备份和当前 Revision |

## 4. 健康检查到底是什么

“健康审查”不是一个单独的第三方组件，准确说是两层组合：

### 4.1 代码里的健康接口

`app/main.py` 定义了两个 FastAPI 路由：

| 接口 | 代码行为 | 适合用途 |
|---|---|---|
| `/health/live` | 只返回 `{"status":"ok"}`，不访问数据库/Redis | 判断 Python 进程是否仍能响应；依赖故障时不应因此重启进程 |
| `/health/ready` | 执行数据库 `SELECT 1` 和 Redis `PING`；失败返回 503 | 判断实例是否有资格接收业务流量 |

`/health/ready` 的 503 来自应用代码的 `HTTPException`，不是 Docker 自己生成的业务响应。

### 4.2 Docker Compose 的健康探针

`compose.production.yaml` 和 `compose.public-ip.yaml` 的 `app.healthcheck` 会在容器内运行
镜像虚拟环境中的 Python 标准库请求（直接调用 `/app/.venv/bin/python`，不触发 `uv` 依赖同步）：

```text
http://127.0.0.1:8000/health/ready
Host: ALLOWED_HOSTS 数组的第一个值
```

Docker 根据命令退出码给容器标记 `starting`、`healthy` 或 `unhealthy`。Nginx 使用：

```yaml
depends_on:
  app:
    condition: service_healthy
```

因此 app 不健康时 Nginx 不会启动。若使用本机依赖覆盖层，app 还会等待 PostgreSQL 和 Redis
各自的 Docker healthcheck 通过。

这解释了日志中的表象：应用进程已经运行，但数据库/Redis 连接被拒绝，readiness 返回 503，
Docker 标记 app 不健康，Nginx 因依赖条件未满足而没有对外提供服务。

## 5. 为什么会出现 DNS 问题

日志里至少有三种不同的“名称解析”，不要混在一起：

1. **Docker 构建包索引 DNS**：`uv sync` 要访问 `files.pythonhosted.org` 或 PyPI 镜像。宿主机
   能 `curl` 不代表 BuildKit/容器网络命名空间能解析；Docker daemon 的 DNS 转发、ECS 内网
   DNS、出口防火墙或临时网络故障都可能造成 `dns error`。
2. **公网域名 DNS**：备案通过并配置 A 记录后，`blog.example.com` 才解析到 ECS/EIP。没有域名
   时直接访问 IP，不需要这一层；备案本身不会自动创建 DNS 记录。
3. **Compose 内部服务 DNS**：本机依赖覆盖层中，app 通过 `postgres` 和 `redis` 解析容器服务名，
   这由 Docker bridge 网络提供，不是公网 DNS。

推荐诊断顺序：

```bash
# 宿主机：确认出口/DNS，不输出任何 Secret
getent hosts pypi.org files.pythonhosted.org
curl -I --connect-timeout 5 https://pypi.org/simple/fastapi/

# 容器：确认 Docker 网络命名空间的解析和 HTTPS
docker run --rm python:3.13-slim python -c \
  "import socket; print(socket.gethostbyname('files.pythonhosted.org'))"
docker run --rm python:3.13-slim python -c \
  "import urllib.request; print(urllib.request.urlopen('https://files.pythonhosted.org', timeout=10).status)"

# Docker daemon 配置只读检查；不要直接覆盖文件
sudo cat /etc/docker/daemon.json
docker info
```

如果只有容器解析失败，再考虑在 `/etc/docker/daemon.json` 的现有 JSON 中合并 `dns`，保留
原有 `registry-mirrors` 等键，执行 JSON 校验后重启 Docker。若只是某个包站点不可达，可临时
传入 `UV_INDEX_URL`；这不是 DNS 修复，仍应继续确认 ECS 的 DNS 和出口策略。

备案后验证公网域名：

```bash
dig +short <APP_DOMAIN>
curl -I "http://<APP_DOMAIN>/"
curl -fsS "https://<APP_DOMAIN>/health/live"
```

`dig` 返回 EIP 只说明 DNS 记录生效，不能替代证书、Nginx、数据库和 Redis 验收。

## 6. 公网 IP、私有 IP 和“外部数据库”的关系

ECS 的公网 IP/EIP 是互联网入口，浏览器通过它访问 Nginx；ECS 私有 IP 属于 VPC，只在云
网络内部可达。RDS/Tair 虽然是 ECS 之外的托管服务，但只要使用 VPC 内网端点，它们并不是
“部署在外网”。这里的“外部”只是指“不在 FastAPI Compose 里运行”。

本机 Docker 方案则不同：PostgreSQL 和 Redis 与 app 同属一个 Compose 网络，app 应使用
`postgres`、`redis` 服务名，不应使用 `localhost` 或 ECS 公网 IP。

## 7. 当前仓库的修正结果

- 正式 `Dockerfile` 恢复多行 `ENV` 和 `fastapi run` 启动方式；PyPI 镜像改为可选 build arg，
  不把地区镜像永久写死。
- 正式 `compose.production.yaml` 当前直接运行 `app`、`nginx`、`postgres`、`redis` 四个服务；密码由 `compose-prod.env` 注入。
- 正式 `nginx/default.conf.template` 恢复 80 到 HTTPS 跳转、443 TLS、限流和 WebSocket。
- `compose.public-ip.yaml` 与 `nginx/public-ip.conf.template` 专门服务无域名公网 IPv4 的
  HTTP 受控验收。
- `compose.public-ip.local-services.yaml` 只保留给无域名临时验收；正式域名部署不要再叠加它。

两条启动路径的完整指令见 [公网 IP 与域名运行文档](02-公网IP与域名运行文档.md)。

如果要逐条学习附件中的命令，参阅 [错误指令学习与排查流程](03-错误指令学习与排查流程.md)。
该文档按“确认对象 → 只读检查 → 判断 → 修复 → 验证”重排命令，并标出哪些命令会修改系统、
停止服务、写入数据库或泄露 Secret。
