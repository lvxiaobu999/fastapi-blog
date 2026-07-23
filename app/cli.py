"""项目管理命令入口。

本模块提供类似 Django ``createsuperuser`` 的交互式管理员创建命令；它复用用户
Service 的校验、密码哈希和事务逻辑，不负责创建数据库表或执行迁移。
"""

import argparse
import asyncio
from getpass import getpass

from pydantic import ValidationError

from app.db.session import AsyncSessionLocal, engine
from app.schemas.user import UserCreate
from app.services.users import UserAlreadyExistsError, create_user


def build_parser() -> argparse.ArgumentParser:
    """构造管理命令解析器，后续可以在同一入口继续增加其他子命令。"""

    parser = argparse.ArgumentParser(description="FastAPI Blog 管理命令")
    parser.add_argument("command", choices=["create-admin"], help="要执行的管理操作")
    return parser


async def create_admin_interactively() -> int:
    """交互读取管理员资料并写入当前环境数据库；成功返回进程状态码 0。

    管理员创建命令是受信任的服务器端入口，所以可以传入 ``is_admin=True``；公开
    HTTP 注册接口永远不传这个参数。密码通过 ``getpass`` 读取，终端不会回显明文。
    """

    username = input("用户名: ").strip()
    email = input("邮箱: ").strip()
    password = getpass("密码（至少 8 个字符）: ")
    password_confirm = getpass("再次输入密码: ")
    if password != password_confirm:
        print("创建失败：两次输入的密码不一致。")
        return 1

    try:
        data = UserCreate(username=username, email=email, password=password)
    except ValidationError as exc:
        # 只展示字段校验信息；Pydantic 错误不会包含数据库凭据或密码哈希。
        messages = "；".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        print(f"创建失败：{messages}")
        return 1

    async with AsyncSessionLocal() as session:
        try:
            user = await create_user(session, data, is_admin=True)
        except UserAlreadyExistsError:
            print("创建失败：用户名或邮箱已存在。")
            return 1

    print(f"管理员 {user.username} 创建成功。")
    return 0


async def async_main() -> int:
    """解析参数并执行异步管理命令，最后释放数据库连接池。

    CLI 与 FastAPI 共用同一 AsyncEngine 配置，保证命令操作的是当前 ENV 指向的数据库；
    它不会调用 ``create_all``，表结构仍由 Alembic 管理。
    """

    args = build_parser().parse_args()
    try:
        if args.command == "create-admin":
            return await create_admin_interactively()
        return 1
    finally:
        await engine.dispose()


def main() -> None:
    """同步命令入口，将异步数据库操作运行在独立事件循环中。"""

    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
