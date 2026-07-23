"""JWT 认证接口契约；只描述登录响应，不暴露 Token 内部或用户敏感字段。"""

from pydantic import BaseModel


class TokenResponse(BaseModel):
    """OAuth2 Password Flow 登录成功后的公开响应。

    只返回客户端发起后续请求所需的两项信息，不返回用户对象、密码哈希、JWT Payload
    或服务端密钥。Token 本身虽然可被客户端持有，但生产环境必须通过 HTTPS 传输。
    """

    # access_token 是带签名的 JWT；前端随后放入 Authorization: Bearer Header。
    access_token: str
    # OAuth2 约定使用小写 bearer，客户端据此拼接认证 Header。
    token_type: str = "bearer"
