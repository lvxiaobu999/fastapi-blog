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


class UserPublic(BaseModel):
    """用户可公开的数据"""

    # UserPublic 也会作为 PostResponse.author 的嵌套 Schema 直接接收 User ORM，
    # 因此必须在这一层启用属性读取，不能只配置最外层的 UserResponse。
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str = Field(min_length=1, max_length=50)
    nickname: str
    image_file: str | None
    image_path: str
    is_admin: bool


class UserPrivate(UserPublic):
    """用户隐私数据"""

    email: EmailStr = Field(max_length=254)


class UserResponse(UserPrivate):
    """允许通过 API 返回的用户公开信息。"""

    model_config = ConfigDict(from_attributes=True)


class AdminUserCreate(UserCreate):
    """管理员后台创建用户的请求；角色字段只在管理员接口出现。"""

    nickname: str | None = Field(default=None, min_length=1, max_length=50)
    is_admin: bool = False


class AdminUserUpdate(BaseModel):
    """管理员后台可更新的用户资料与角色，不在这里直接修改密码。"""

    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=254)
    nickname: str | None = Field(default=None, min_length=1, max_length=50)
    is_admin: bool | None = None
