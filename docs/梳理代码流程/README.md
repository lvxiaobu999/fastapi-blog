# 核心代码流程解析

本目录面向正在通过本项目学习 Python 和 FastAPI 的开发者。目标不是只告诉你“代码能做什么”，
而是回答下面这些问题：

- 这个变量保存什么，为什么使用这种类型？
- 这行 Python 或框架 API 实际执行了什么？
- 一次请求从前端进入后，依次经过哪些文件？
- `await` 前后发生了什么，数据库或 Redis 何时真正写入？
- 成功分支返回什么，失败分支为什么是 400、401、403、404 或 503？
- 为什么不能换成看起来更简单的写法？

## 建议阅读顺序

1. [JWT 与 Auth 认证授权代码解析](./01-JWT与Auth认证授权代码解析.md)
2. [Redis Refresh Session 代码解析](./02-Redis-Refresh-Session代码解析.md)
3. [Logging 日志系统代码解析](./03-Logging日志系统代码解析.md)
4. [WebSocket 实时评论代码解析](./04-WebSocket实时评论代码解析.md)

先阅读 JWT，理解“用户是谁”和 Bearer Token；再阅读 Redis，理解 Access Token 过期后如何
保持登录；Logging 可以独立阅读；最后阅读 WebSocket，它同时使用认证、数据库、异步并发和
前端事件协议，综合性最高。

## 本项目统一分层

```text
浏览器
  -> Router：解析 HTTP/WebSocket 参数，决定状态码和权限
  -> Schema/Depends：校验输入，取得当前用户和数据库连接
  -> Service：执行业务规则、事务和数据访问
  -> ORM / Redis：保存状态
  -> Service 返回 ORM/Schema
  -> Router 组装响应
  -> 浏览器
```

阅读时不要只停在 Router。一个登录请求至少会经过：

```text
api_auth.py
  -> services/auth.py
  -> models/user.py + SQLAlchemy
  -> services/refresh_sessions.py
  -> db/redis.py
  -> schemas/auth.py
  -> api_responses.py
```

## 文档标记约定

| 标记 | 含义 |
|---|---|
| “定位” | 变量或 Key 用来找到数据，不表示已经认证 |
| “认证” | 确认请求者是谁，例如验证密码或 JWT |
| “授权” | 已知道是谁后，确认是否允许操作，例如管理员判断 |
| “原子” | 操作执行时不会被另一个请求插入中间状态 |
| “幂等” | 同一操作重复执行，最终结果与执行一次相同 |
| “失败关闭” | 数据异常或无法确认时拒绝访问，而不是猜测为有效 |
| “事务” | 一组数据库操作全部成功才提交，否则回滚 |

## 推荐阅读方法

1. 左侧打开本文档，右侧打开文档链接的源码。
2. 先按“完整调用链”从头走一遍，不急着研究每个库函数。
3. 第二遍阅读“关键对象与变量”，把变量和实际数据对应起来。
4. 第三遍在 VSCode 给 Router、Service、数据库/Redis 调用打断点。
5. 使用测试数据调试，不在断点、日志、截图中暴露真实密码、Token、Cookie 或连接 URI。

## 四个主题之间的关系

```text
登录表单
  -> Auth 校验密码
  -> JWT 签发短期 Access Token
  -> Redis 保存长期 Refresh Session

受保护 HTTP 请求
  -> Depends 提取 Access Token
  -> JWT 验证身份
  -> Service 执行业务
  -> Logging 自动记录 request_id

实时评论
  -> HTTP 加载历史评论
  -> WebSocket 第一条消息发送 Access Token
  -> JWT 验证身份
  -> Service 提交数据库
  -> WebSocket 广播新评论
  -> Logging 记录服务端异常和访问事件
```

## 源码入口

- 认证 Router：[app/routers/api_auth.py](../../app/routers/api_auth.py)
- JWT Service：[app/services/auth.py](../../app/services/auth.py)
- Redis Session：[app/services/refresh_sessions.py](../../app/services/refresh_sessions.py)
- 日志基础设施：[app/core/logging.py](../../app/core/logging.py)
- WebSocket Router：[app/routers/comments.py](../../app/routers/comments.py)
- WebSocket 房间管理：[app/websockets/comments.py](../../app/websockets/comments.py)

