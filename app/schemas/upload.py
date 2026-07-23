"""文件上传响应契约；只返回公开访问地址，不暴露服务器文件系统路径。"""

from pydantic import BaseModel


class ImageUploadResponse(BaseModel):
    """帖子编辑器图片上传成功后的响应。"""

    url: str
