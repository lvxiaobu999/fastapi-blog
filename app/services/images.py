"""帖子图片校验和本地存储服务。

本模块只负责限制文件大小、识别允许的图片签名并生成随机文件名；不处理 HTTP 身份
认证，也不负责数据库记录。图片通过应用已有的 ``/media`` 静态挂载公开读取。
"""

from uuid import uuid4

from anyio import to_thread
from fastapi import UploadFile

from app.templating import APP_DIR

MAX_IMAGE_BYTES = 5 * 1024 * 1024
POST_IMAGE_DIR = APP_DIR / "media" / "post_images"


class InvalidImageError(ValueError):
    """上传内容不是允许的图片格式，或超过大小限制。"""


def _detect_extension(content: bytes) -> str | None:
    """根据文件魔数识别图片类型，避免只信任可伪造的扩展名和 Content-Type。"""

    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return None


async def save_post_image(upload: UploadFile) -> str:
    """校验并保存帖子图片，成功时返回浏览器可访问的相对 URL。

    读取 ``MAX_IMAGE_BYTES + 1`` 可以判断超限而不把任意大文件完整载入内存。随机文件名
    避免用户文件名中的路径字符，也防止同名覆盖。磁盘写入放到工作线程以免阻塞事件循环。
    """

    content = await upload.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise InvalidImageError("Image must not exceed 5 MB")

    extension = _detect_extension(content)
    if extension is None:
        raise InvalidImageError("Only PNG, JPEG, GIF and WebP images are allowed")

    filename = f"{uuid4().hex}{extension}"
    target = POST_IMAGE_DIR / filename

    def write_image() -> None:
        POST_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    await to_thread.run_sync(write_image)
    return f"/media/post_images/{filename}"
