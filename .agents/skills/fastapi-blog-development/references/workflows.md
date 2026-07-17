# 任务工作流

## 新增资源或功能

1. 定义 API 契约、认证要求、所有权、状态码和失败场景。
2. 新增或修改 ORM Model 与数据库约束。
3. 生成并检查 Alembic Migration。
4. 新增请求与响应 Schema，确保不泄露内部字段。
5. 新增 Service 操作，设置明确事务边界。
6. 新增 Router 与依赖。
7. 测试成功、参数校验、未认证、无权限和资源不存在。
8. 更新 API 和请求链文档。

## 修改现有接口

1. 检查 Router、Schema、Service、Model、调用方和测试。
2. 判断是否向后兼容。
3. 保持响应过滤和状态码语义。
4. 同时检查分页数量与查询筛选条件。
5. 先增加回归测试，再考虑更大范围清理。

## 诊断 500 错误

1. 获取完整 Traceback 和最后一个异常。
2. 用单个最小请求复现。
3. 判断失败边界：校验、依赖、认证、Service、ORM、数据库或序列化。
4. 数据库错误检查 `.env` 目标（不输出凭据）、Compose 状态与日志、端口映射、实际数据库名称和 Alembic 状态。
5. 如果用户只要求诊断，先解释根因，不擅自实施修复。

## 修改认证

1. 分别梳理登录签发 Token 和受保护请求验证链。
2. 保持密码哈希与通用认证失败响应。
3. 明确过期时间、Claims、撤销影响和传输方式。
4. 使用 Cookie 时定义 Secure、HttpOnly、SameSite 和 CSRF 防护。
5. 测试 Token 缺失、格式错误、过期、用户错误、用户禁用和正常情况。

## 修改数据库结构

1. 确认目标数据库。
2. 修改 Model，并确保 Alembic Metadata 已导入该 Model。
3. 设置项目根目录 `PYTHONPATH`。
4. 执行 `revision --autogenerate`。
5. 检查 upgrade、downgrade、约束、索引、默认值和数据迁移需求。
6. 只应用到已获授权的本地或测试数据库。
7. 验证 Revision 和受影响接口。

## 改进测试隔离

1. 使用 `TEST_SQLALCHEMY_DATABASE_URI` 创建 Engine。
2. 将测试 Session 工厂绑定到该 Engine。
3. 通过 `app.dependency_overrides` 覆盖 `get_db`。
4. 使用迁移或受控 Setup 创建结构。
5. 每个用例通过事务回滚或确定性清理隔离。
6. 移除执行顺序和历史数据依赖。
7. 测试结束后清除依赖覆盖。

## 验证矩阵

| 改动类型 | 最低验证要求 |
| --- | --- |
| Router/Schema | 目标 API 测试、422 路径、响应结构 |
| 认证/权限 | 有效 Token、401、403、所有权 |
| Service/查询 | 目标测试、筛选和数量行为 |
| Model | 迁移检查、受影响 API 测试 |
| Migration | upgrade、downgrade、当前 Revision |
| 配置/启动 | 导入应用、数据库连接、OpenAPI |
| 文档 | 前置条件准确、破坏性命令有警告 |

数据库不一致导致验证受阻时，不得声称已经完成全部验证。

