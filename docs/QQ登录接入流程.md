# QQ 登录接入流程

本文说明本项目已经实现的 QQ 互联 OAuth 2.0 登录、QQ 互联应用申请、环境变量、首次登录建号、部署迁移、安全边界和故障排查。QQ 控制台的菜单名称与审核要求可能调整，申请页面应以 QQ 互联控制台当时显示的规则为准。

## 1. 这项功能解决什么问题

用户点击“使用 QQ 登录”后，会前往 QQ 官方页面确认授权。博客不会接触 QQ 密码，也不会把 QQ access token 当作博客登录凭证。QQ 只负责证明“当前浏览器控制某个 QQ 身份”，证明成功后，本项目仍签发自己的 Access JWT 和 Redis Refresh Session。

本项目现有两条登录方式可以同时使用：

```text
账号密码登录
  -> POST /api/auth/token
  -> 本站 Access JWT + Redis Refresh Session

QQ 登录
  -> QQ 官方授权页
  -> GET /api/auth/qq/callback
  -> 按 QQ openid 找到/创建本站用户
  -> 本站 Access JWT + Redis Refresh Session
```

QQ 登录不是 SMTP 邮箱授权。下面两个密钥用途不同，不能混用：

- `QQ_CLIENT_SECRET`：QQ 互联应用的 App Key/Client Secret；
- `MAIL_PASSWORD` 或 `SMTP_PASSWORD`：QQ 邮箱 SMTP 授权码。

## 2. 当前代码实现范围

已实现：

- QQ 授权入口与回调接口；
- Redis 一次性 `state`，用于防止 OAuth 回调伪造和重放；
- 服务端交换 QQ access token、查询 openid 和昵称；
- 第一次 QQ 登录自动创建普通博客用户；
- 后续使用同一 openid 时复用原用户；
- 创建本站 Refresh Session，并由登录页换取短期 Access JWT；
- 登录页、注册页、全局登录/注册弹窗和后台登录弹窗的 QQ 入口；
- 未配置完整 QQ 应用参数时隐藏按钮；
- 数据库唯一约束，防止一个 openid 绑定多个博客用户。

当前没有实现“把已有密码账号手动绑定 QQ”。系统不会按昵称自动合并，也不会猜测某个 QQ 用户是否等于某个本地用户，因为错误合并会造成账号接管。如果同一个人先注册了密码账号，之后又第一次使用 QQ 登录，当前会得到一个独立的 QQ 账号。

## 3. 完整请求链

```text
浏览器点击“使用 QQ 登录”
  -> GET /api/auth/qq/login?next_path=/原页面
  -> FastAPI 生成随机 state
  -> Redis SET state -> 原页面，TTL 默认 600 秒
  -> 302 跳转 https://graph.qq.com/oauth2.0/authorize

用户在 QQ 页面同意授权
  -> QQ 携带 code + state 回调
  -> GET /api/auth/qq/callback
  -> Redis GETDEL 原子消费 state
  -> 服务端使用 code 换 QQ access_token
  -> 服务端使用 access_token 查询 openid
  -> 服务端查询 QQ 昵称
  -> users.provider + users.provider_user_id 查询用户
       -> 已存在：复用用户
       -> 不存在：创建普通用户
  -> Redis 创建本站 Refresh Session
  -> Set-Cookie: refresh_token=...; HttpOnly; Path=/api
  -> 303 /login?qq=success&next=/原页面

登录页 auth.js
  -> POST /api/auth/refresh（浏览器自动携带 HttpOnly Cookie）
  -> 得到本站短期 Access JWT
  -> 保存到 localStorage
  -> 跳回原页面
```

QQ access token 只在单次服务端调用链中短暂存在：不会写数据库、Redis、Cookie、浏览器 URL 或应用日志。回调 URL 中也不会携带本站 JWT。

## 4. QQ 互联控制台申请

1. 打开 QQ 互联平台并使用开发者账号登录。
2. 完成开发者资料或主体认证。
3. 创建“网站应用”，填写网站名称、简介、图标和正式访问地址。
4. 按控制台要求完成域名所有权、备案或网站审核。
5. 在应用回调地址中填写：

   ```text
   https://你的域名/api/auth/qq/callback
   ```

6. 应用审核通过后取得 App ID 和 App Key。项目中分别对应 `QQ_CLIENT_ID` 和 `QQ_CLIENT_SECRET`。

回调地址必须逐字一致，包括：

- `http` 或 `https`；
- 域名；
- 端口（如果不是默认端口）；
- `/api/auth/qq/callback` 路径；
- 是否带末尾斜杠。

推荐只登记不带末尾斜杠的正式 HTTPS 地址。

## 5. 公网 IP 阶段能否使用

当前项目的生产配置明确要求 `QQ_REDIRECT_URI` 使用 HTTPS。因此备案未完成、只有公网 IP HTTP 的临时部署模式不会启用正式 QQ 登录。

这是有意设置的安全边界：OAuth 授权码和本站 Refresh Cookie 不应经过公网明文 HTTP；同时 QQ 互联的网站应用通常还会审核域名和站点信息。建议等域名备案、TLS 证书和 QQ 网站应用审核完成后再开启。

本地代码联调可以配置：

```text
http://127.0.0.1:8000/api/auth/qq/callback
```

但 QQ 控制台未必允许 localhost/127.0.0.1 回调。若平台拒绝，应使用一套测试域名或受控的公网 HTTPS 测试环境，并把控制台和本地配置改为完全一致的地址。

## 6. 环境变量

开发环境可以直接写入项目根目录 `.env`（本项目已预留带中文说明的配置模板）；生产环境建议写入 ECS 的
`/opt/fastapi-blog/config/app.env` 或密钥管理服务。两个文件都不要提交到 Git，也不要把真实
`QQ_CLIENT_SECRET` 粘贴到聊天、截图或日志中：

```dotenv
# QQ 互联应用的 App ID，也兼容变量名 QQ_APP_ID。
QQ_CLIENT_ID=<QQ_APP_ID>

# QQ 互联应用的 App Key/Client Secret，也兼容变量名 QQ_APP_KEY。
# 这是敏感值，不要打印、截图或写进 Compose YAML。
QQ_CLIENT_SECRET=<QQ_APP_KEY>

# 必须与 QQ 互联控制台登记的回调地址逐字一致；生产只能使用 HTTPS。
QQ_REDIRECT_URI=https://blog.example.com/api/auth/qq/callback

# OAuth state 在 Redis 中的有效期。一般保留默认 600 秒。
QQ_OAUTH_STATE_TTL_SECONDS=600

# 服务端请求 QQ 接口的超时时间。一般保留默认 10 秒。
QQ_HTTP_TIMEOUT_SECONDS=10
```

`.env` 中的三个核心变量默认保持注释状态，拿到 QQ 互联凭据后再取消注释并替换占位值；
`QQ_OAUTH_STATE_TTL_SECONDS` 和 `QQ_HTTP_TIMEOUT_SECONDS` 是非敏感参数，可以直接使用模板中的默认值。
如果使用 `QQ_APP_ID`/`QQ_APP_KEY` 这两个控制台常见名称，它们会分别兼容映射到
`QQ_CLIENT_ID`/`QQ_CLIENT_SECRET`。

四个 QQ 接口地址已有官方默认值，通常无需配置：

```dotenv
QQ_AUTHORIZE_URL=https://graph.qq.com/oauth2.0/authorize
QQ_TOKEN_URL=https://graph.qq.com/oauth2.0/token
QQ_OPENID_URL=https://graph.qq.com/oauth2.0/me
QQ_USERINFO_URL=https://graph.qq.com/user/get_user_info
```

只要 Client ID、Client Secret、回调地址缺少任意一项，应用就会在配置校验阶段拒绝“半配置”状态。三项都不提供时，普通账号密码登录继续可用，页面不会显示 QQ 按钮。

## 7. 数据库变化和首次登录数据

迁移文件：

```text
migrations/versions/20260824_01_add_qq_openid.py
migrations/versions/20260824_02_allow_qq_only_passwordless.py
migrations/versions/20260824_03_generalize_provider_identity.py
migrations/versions/20260824_04_require_provider_identity_pair.py
```

迁移向 `users` 表增加：

```text
provider VARCHAR(32) NULL
provider_user_id VARCHAR(128) NULL
UNIQUE (provider, provider_user_id)
CHECK ((provider IS NULL AND provider_user_id IS NULL)
       OR (provider IS NOT NULL AND provider_user_id IS NOT NULL))
```

已有密码用户的值保持 `NULL`。首次 QQ 登录创建用户时：

| 字段 | 值来源 |
| --- | --- |
| `provider` | 第三方平台类型；当前 QQ 登录固定为 `qq` |
| `provider_user_id` | 第三方平台返回的稳定身份 ID；QQ 的 `openid` 会映射到此字段 |
| `nickname` | QQ 用户资料接口返回的昵称，最长 50 字符 |
| `username` | openid SHA-256 摘要派生的稳定内部用户名 |
| `email` | `@qq-accounts.internal` 内部占位邮箱 |
| `hashed_password` | 首次登录为 `NULL`；用户主动设置密码后才保存 Argon2 哈希 |
| `is_admin` | `false`，QQ 登录不能创建管理员 |

QQ 互联接口不保证提供可用于本项目的真实邮箱，因此先使用不可投递的内部占位邮箱。用户应在首次登录后到个人资料中改为自己的真实邮箱，之后才能使用邮箱验证码找回密码。密码重置服务不会向 `@qq-accounts.internal` 地址发送邮件。

`20260824_02_allow_qq_only_passwordless.py` 会把 `hashed_password` 改为可空，并将历史 QQ
账号的随机占位哈希清为 `NULL`；普通密码账号的哈希不会被修改。执行迁移前必须备份并确认目标数据库。

## 8. 部署步骤

### 8.1 部署前

1. 备份 ECS Compose 中的 PostgreSQL（逻辑备份或数据目录快照）。
2. 确认域名和 TLS 已生效。
3. 确认 QQ 控制台回调地址与 `QQ_REDIRECT_URI` 完全相同。
4. 把三项 QQ 配置注入 `app.env`，不要写入 Compose 文件。
5. 检查待执行迁移内容。

### 8.2 执行迁移

下面命令会修改数据库结构，必须先确认连接的是当前 Compose `postgres` 数据库：

```bash
export APP_DOMAIN=www.sanwan.xyz
export APP_IMAGE_TAG=$(git rev-parse --short HEAD)
export COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml \
  run --rm app uv run --no-sync alembic upgrade head
```

### 8.3 重新构建并启动

```bash
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml up -d --build --force-recreate
docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml ps
```

Nginx 已把 `/api/` 转发给 FastAPI，不需要为 QQ 回调单独增加 location。需要保证公网能访问：

```text
GET https://你的域名/api/auth/qq/login
GET https://你的域名/api/auth/qq/callback
```

不要直接在地址栏手工访问 callback；它必须携带 QQ 签发的短期 `code` 和本项目生成的 `state`。

## 9. 验收步骤

1. 未登录时打开首页登录弹窗，确认出现“使用 QQ 登录”。
2. 点击按钮，确认浏览器进入 `graph.qq.com` 官方授权页。
3. 同意授权，确认回到博客且导航栏显示登录状态。
4. 打开个人资料，确认昵称来自 QQ，并把内部占位邮箱改为真实邮箱。
5. 退出后再次点击 QQ 登录，确认复用原账号而不是新增用户。
6. 查询数据库时只检查是否存在绑定，不输出完整 openid：

   ```sql
   SELECT id, username,
          provider = 'qq' AND provider_user_id IS NOT NULL AS has_qq_login
   FROM users
   ORDER BY id DESC
   LIMIT 10;
   ```

7. 检查浏览器地址栏、应用日志和数据库，确认没有 QQ access token。

## 10. 常见问题排查

### 页面没有 QQ 登录按钮

- 三项配置必须同时存在；
- 修改 `app.env` 后需要重启/重建 app 容器；
- 检查容器内“是否存在”，不要输出 Secret：

  ```bash
  docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml exec app \
    sh -c 'test -n "$QQ_CLIENT_ID" && test -n "$QQ_CLIENT_SECRET" && test -n "$QQ_REDIRECT_URI" && echo "QQ OAuth configured"'
  ```

### QQ 提示回调地址不合法

逐项比较控制台和 `QQ_REDIRECT_URI` 的协议、域名、端口、路径和末尾斜杠。经过 Nginx 反向代理不改变控制台登记的公网地址；控制台必须填写用户浏览器真正访问的 HTTPS 地址，而不是容器名或 `http://app:8000`。

### 回到博客后显示“QQ 登录未完成”

- `state` 已超过默认 10 分钟；
- 浏览器刷新或重复打开了同一个 callback；
- Redis 容器中的 state 丢失；
- 用户在 QQ 页面拒绝授权；
- ECS 无法访问 `graph.qq.com`；
- QQ Token、openid 或用户资料接口返回了异常格式。

重新从博客的 QQ 按钮发起一次，不要重复刷新 callback URL。

### QQ 授权成功但 `/refresh` 返回 401

- 检查回调响应是否设置 `refresh_token` Cookie；
- 正式生产必须是 HTTPS 且 `AUTH_COOKIE_SECURE=true`；
- Cookie Path 应保持 `/api`；
- 检查 `redis` 容器是否可写，以及 Refresh Session TTL 是否正常；
- 不要把 QQ access token 当作本站 Refresh Token。

### 同一个人出现两个博客账号

这是当前“禁止自动合并”的设计结果：密码账号和首次 QQ 登录账号没有可信的共同标识。当前版本不要手工改数据库合并。后续如果需要，可增加“用户已登录并再次验证密码后绑定 QQ”的专用流程。

## 11. 安全设计

- 不获取、不保存 QQ 密码；
- `QQ_CLIENT_SECRET` 只在服务端使用；
- 随机 `state` 存 Redis，短 TTL，并通过 `GETDEL` 一次性消费；
- `next_path` 只允许站内路径，防止开放重定向；
- QQ access token 不持久化、不打印；
- callback 不在 URL 中返回本站 JWT；
- Refresh Token 使用 HttpOnly Cookie；
- QQ 用户默认是普通用户，不能由第三方资料提升为管理员；
- `(provider, provider_user_id)` 使用数据库联合唯一约束兜住并发首次登录；
- 生产回调强制 HTTPS。

## 12. 关键代码文件

| 文件 | 作用 |
| --- | --- |
| `app/core/config.py` | QQ Client、回调地址、state TTL 和 HTTP 超时配置 |
| `app/services/qq_oauth.py` | state、QQ API、响应解析和首次建号编排 |
| `app/routers/api_auth.py` | QQ 登录入口、callback、Refresh Cookie |
| `app/services/users.py` | 按 openid 查询和创建 QQ 用户 |
| `app/models/user.py` | `provider` 与 `provider_user_id` ORM 字段 |
| `migrations/versions/20260824_03_generalize_provider_identity.py` | 从 QQ 专用字段迁移到通用身份字段 |
| `migrations/versions/20260824_04_require_provider_identity_pair.py` | 要求两个身份字段成对出现 |
| `app/static/js/auth.js` | callback 后换取本站 Access JWT 并返回原页面 |
| `app/templates/login.html`、`layout.html` | 普通页面 QQ 入口 |
| `app/templates/admin/layout.html` | 后台重新登录 QQ 入口 |
| `tests/test_qq_oauth.py` | state、解析、建号、回调和页面入口测试 |

## 13. 本地验证命令

```powershell
$env:PYTHONPATH = (Get-Location).Path
uv run pytest tests/test_qq_oauth.py tests/test_auth.py tests/test_pages.py -q
uv run ruff check app/core/config.py app/templating.py app/models/user.py \
  app/services/users.py app/services/qq_oauth.py app/services/password_reset.py \
  app/routers/api_auth.py migrations/versions/20260824_03_generalize_provider_identity.py \
  migrations/versions/20260824_04_require_provider_identity_pair.py \
  tests/test_qq_oauth.py tests/test_config.py
node --check app/static/js/auth.js
```

自动化测试会模拟 QQ 响应，不会连接真实 QQ，也不会使用真实 App Key。真实联调必须在你自己的 QQ 互联应用、审核通过的回调地址和 HTTPS 环境中完成。

## 14. QQ-only 用户设置本地密码

QQ 首次登录不需要设置密码。账号会保存 `provider='qq'` 和 `provider_user_id`，而 `users.hashed_password` 保持为 `NULL`；
账号密码登录遇到这种用户时仍返回统一的“用户名或密码错误”，不会暴露账号类型。用户可以一直
使用 QQ 登录，也可以在已登录状态下主动启用账号密码登录。

### 14.1 设置流程

1. 用户先通过 QQ 完成登录。
2. 个人菜单始终只有一个“密码”入口；它根据 `/api/auth/me` 返回的 `has_password=false`
   在“设置密码”和“修改密码”之间切换文案与表单模式，不会同时出现两个按钮。
3. 提交两次新密码到 `POST /api/auth/password/set`，不要求填写旧密码或邮箱验证码。
4. 服务端使用 Argon2 保存新哈希，并提升 Redis Refresh Session 代次，撤销所有旧设备会话。
5. 当前 Refresh Cookie 被清除，前端删除 Access JWT，用户需要重新登录。

设置接口只允许已绑定 `provider/provider_user_id` 且尚未设置密码的账号调用；普通密码账号必须继续使用
`POST /api/auth/password` 的旧密码校验流程。设置密码接口的请求体为：

```json
{
  "new_password": "至少 8 个字符",
  "confirm_password": "再次输入相同密码"
}
```

### 14.3 为什么要拆成两个字段

`provider` 是平台命名空间，当前值为 `qq`；`provider_user_id` 是该平台返回的稳定身份值，QQ API 中称为 `openid`。不能只保存 `provider_user_id`，因为 QQ 和微信都可能返回相同格式甚至相同文本的 ID。数据库约束使用 `UNIQUE(provider, provider_user_id)`，含义是“同一平台的同一身份只能绑定一个本站账号”，但不同平台可以拥有相同的 ID。

代码会把 `provider` 统一转成小写并去除两端空格，同时要求两个字段必须成对出现。这样 `QQ`、` qq ` 和 `qq` 不会被误认为三个平台，也不会产生只有平台名、没有平台用户 ID 的半成品身份记录。

当前字段放在 `users` 表，适合一个用户只绑定一个第三方身份的阶段。如果产品以后要求“同一个本站账号同时绑定 QQ 和微信”，不要继续在 `users` 表增加 `wechat_user_id`，应拆出 `user_identities` 表：每行保存 `user_id`、`provider`、`provider_user_id`，并对 `(provider, provider_user_id)` 建立唯一约束。那是一次更大的数据模型升级，本次暂不引入。

QQ Service 的实际映射为：

```text
QQ /oauth2.0/me 返回 openid
  -> QQProfile.openid（第三方 API 领域名）
  -> provider="qq" + provider_user_id=openid（本站统一模型）
  -> users Service 按复合身份查询或创建
```

`access_token` 只用于本次服务端请求，不写入 users、Redis、Cookie 或日志；昵称只用于展示，不能作为身份主键。首次登录不会按邮箱或昵称自动合并已有密码账号，避免占位邮箱、可修改昵称导致账号接管。

### 14.2 邮箱在什么环节使用

设置密码本身不强制邮箱验证码，因为当前 QQ 会话已经完成身份认证。邮箱验证码用于“忘记密码”：
QQ 用户应先在个人资料中绑定并验证真实邮箱，之后才能通过密码重置流程找回本地密码。
`qq-accounts.internal` 是首次登录的内部占位邮箱，不能接收邮件。

如果用户未登录，不能只凭用户名直接设置密码；应重新完成 QQ 登录，或使用已经验证的邮箱走密码重置。
