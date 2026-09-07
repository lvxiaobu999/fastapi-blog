# Nginx 与 HTTPS 配置

正式 ECS 命令先定义 Compose 包装函数，确保读取生产参数文件：

```bash
export COMPOSE_ENV=/opt/fastapi-blog/config/compose-prod.env
dc() { docker compose --env-file "$COMPOSE_ENV" -f compose.production.yaml "$@"; }
```

仓库已提供 `nginx/default.conf.template`，Compose 会通过 `APP_DOMAIN` 生成实际配置。它支持
FastAPI HTTP、5 MB 图片上传和 WebSocket 评论。以下内容用于解释关键设置：

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 80;
    listen [::]:80;
    server_name example.com www.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://example.com$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name example.com www.example.com;

    ssl_certificate     /etc/nginx/tls/fullchain.pem;
    ssl_certificate_key /etc/nginx/tls/privkey.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;

    # 项目当前最大图片为 5 MB，留出 multipart 边界开销；过大会扩大滥用风险。
    client_max_body_size 6m;

    add_header X-Content-Type-Options nosniff always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;
    add_header X-Frame-Options SAMEORIGIN always;
    # 确认全站 HTTPS 且无 HTTP 子资源后再启用并逐步增加 max-age。
    # add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://app:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_connect_timeout 5s;
    }
}
```

`Upgrade` 和 `Connection` 对 `/api/posts/{id}/comments/ws` 必不可少。浏览器在 HTTPS 页面会自动
使用 `wss://`，无需额外公开端口。

## 启用配置

```bash
dc run --rm nginx nginx -t
dc up -d nginx
dc exec nginx nginx -t
dc exec nginx nginx -s reload
```

如果第一次还没有证书，不能直接启动引用不存在证书文件的 443 配置。先使用仅 80 的临时配置
完成域名验证/签证书，再替换为完整配置并执行 `nginx -t`。

## 是否让 Nginx 直接提供静态文件

当前应用自己挂载 `/static` 和 `/media`，首次上线让 Nginx 全部反代最不容易出现路径差异。
后续可让 Nginx 直接读取只读静态资源，但 `/media` 需要共享挂载并保持权限一致。多 ECS 时应将
上传迁移 OSS，不能依赖某一台机器本地目录。

## 代理后的应用注意事项

- 生产 `AUTH_COOKIE_SECURE=true`，否则安全 Cookie 配置不符合 HTTPS 生产要求。
- 只信任来自自有 Nginx/SLB 的转发头。若以后把 Uvicorn 暴露给其他来源，需要显式限制
  forwarded-allow-ips，避免伪造协议和客户端 IP。
- 若前面再增加 SLB/CDN/WAF，应明确哪一层终止 TLS、真实客户端 IP 头及可信代理链。
- 当前日志记录 path、状态码、耗时和请求 ID；Nginx 日志不要记录 Authorization/Cookie。
