"""用户接口的数据契约。

请求 Schema 负责校验客户端输入，响应 Schema 负责限制公开字段；
明文密码只允许出现在请求中，密码哈希永远不能进入响应。
"""

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    """创建请求与响应共同包含的用户基础字段。"""

    username: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(max_length=254)


class UserCreate(UserBase):
    """创建用户时需要提交的字段。"""

    # 接口接收明文密码，但 Service 必须先哈希，再写入 User.hashed_password。
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    """用户部分更新参数；未传入的字段保持原值。"""

    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=254)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    nickname: str | None = Field(default=None, min_length=1, max_length=50)
    # 传入 null 表示恢复默认头像，因此 image_file 需要允许 None。
    image_file: str | None = Field(default=None, max_length=200)


class UserResponse(UserBase):
    """允许通过 API 返回的用户公开信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str
    image_file: str | None
    image_path: str
    is_admin: bool
