"""评论输入和公开响应契约；不暴露用户邮箱、密码或 Token。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommentCreate(BaseModel):
    """校验 WebSocket 中提交的评论正文。"""

    content: str = Field(min_length=1, max_length=1000)
    parent_id: int | None = Field(default=None, gt=0)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        """去除首尾空白，并拒绝只包含空白字符的评论。"""

        normalized = value.strip()
        if not normalized:
            raise ValueError("评论内容不能为空")
        return normalized


class WebSocketAuthMessage(BaseModel):
    """浏览器建立连接后发送的第一条认证消息。"""

    type: Literal["authenticate"]
    token: str = Field(min_length=1)


class CommentCreateMessage(CommentCreate):
    """认证成功后，浏览器通过 WebSocket 发送的评论消息。"""

    type: Literal["comment.create"]


class CommentAuthor(BaseModel):
    """评论区可公开展示的最小作者信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nickname: str
    image_path: str


class CommentResponse(BaseModel):
    """历史列表和实时推送共用的评论响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    created_at: datetime
    post_id: int
    parent_id: int | None
    root_id: int | None
    author: CommentAuthor
    reply_to_author: CommentAuthor | None
