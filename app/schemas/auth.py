"""JWT 认证接口契约；只描述登录响应，不暴露 Token 内部或用户敏感字段。"""

from pydantic import BaseModel, EmailStr, Field, model_validator


class PasswordChangeRequest(BaseModel):
    """当前用户修改密码时提交的旧密码与两次新密码。"""

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordChangeRequest":
        """在进入 Service 前拒绝两次输入不一致的新密码。"""

        if self.new_password != self.confirm_password:
            raise ValueError("New passwords do not match")
        return self


class PasswordSetRequest(BaseModel):
    """QQ-only 用户首次启用账号密码登录时提交的两次新密码。

    该请求不接收 ``current_password``：QQ OAuth 会话已经完成身份认证，接口只允许
    ``hashed_password`` 仍为空的 QQ 用户调用。已设置过密码的用户必须使用修改密码接口。
    """

    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordSetRequest":
        """在进入 Service 前拒绝两次输入不一致的新密码。"""

        if self.new_password != self.confirm_password:
            raise ValueError("New passwords do not match")
        return self


class PasswordResetRequest(BaseModel):
    """请求向已注册邮箱发送一次性密码重置验证码。"""

    email: EmailStr = Field(max_length=254)


class PasswordResetConfirm(BaseModel):
    """使用邮箱验证码提交新密码；验证码只能成功使用一次。"""

    email: EmailStr = Field(max_length=254)
    code: str = Field(pattern=r"^[0-9]{6}$")
    new_password: str = Field(min_length=8, max_length=128)
    confirm_password: str = Field(min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordResetConfirm":
        """在进入 Service 前拒绝两次输入不一致的新密码。"""

        if self.new_password != self.confirm_password:
            raise ValueError("New passwords do not match")
        return self


class TokenResponse(BaseModel):
    """OAuth2 Password Flow 登录成功后的公开响应。

    只返回客户端发起后续请求所需的两项信息，不返回用户对象、密码哈希、JWT Payload
    或服务端密钥。Token 本身虽然可被客户端持有，但生产环境必须通过 HTTPS 传输。
    """

    # access_token 是带签名的 JWT；前端随后放入 Authorization: Bearer Header。
    access_token: str
    # OAuth2 约定使用小写 bearer，客户端据此拼接认证 Header。
    token_type: str = "bearer"
