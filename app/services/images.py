"""帖子与头像图片的校验、本地存储和受控删除服务。

本模块只负责限制文件大小、识别允许的图片签名并生成随机文件名；不处理 HTTP 身份
认证，也不负责数据库记录。图片通过应用已有的 ``/media`` 静态挂载公开读取。
"""

import logging
from pathlib import Path
from uuid import uuid4

from anyio import to_thread
from fastapi import UploadFile

from app.templating import APP_DIR

# ==================== Service 入口导读 ====================
# 上游调用者：api_posts 的编辑器图片上传、api_users 的头像即时上传。
# 本模块只校验图片二进制签名/大小并写磁盘，不判断登录权限，也不更新数据库字段。
# 磁盘写入通过 AnyIO 工作线程执行，避免同步文件操作卡住 FastAPI 事件循环。
# 失败抛 InvalidImageError 交给 Router 转成 400；成功返回随机文件名或公开相对 URL。

MAX_IMAGE_BYTES = 5 * 1024 * 1024
POST_IMAGE_DIR = APP_DIR / "media" / "post_images"
PROFILE_IMAGE_DIR = APP_DIR / "media" / "profile_pics"
logger = logging.getLogger(__name__)


class InvalidImageError(ValueError):
    """上传内容不是允许的图片格式，或超过大小限制。"""


def _detect_extension(content: bytes) -> str | None:
    """确认上传内容确实是站点允许展示的图片，而不是只看用户声称的类型。

    发帖图片和头像上传都依赖它。文件名及 Content-Type 可由客户端伪造，所以检查二进制
    魔数并返回由服务端决定的扩展名；未知内容返回 None，让上层拒绝保存。
    """

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
    """支持文章编辑器上传正文配图并立即插入可访问地址。

    用户选择图片后，编辑器需要一个 URL 写入正文。函数先限制大小并验证真实格式，再生成
    随机文件名以避免同名覆盖和路径注入，在线程中写磁盘，最后返回 ``/media/...`` 地址。
    读取 ``MAX_IMAGE_BYTES + 1`` 能判断超限而无需把任意大文件完整载入内存。
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
        """在线程中完成目录创建和正文图片写盘，避免阻塞异步请求处理。"""

        POST_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    await to_thread.run_sync(write_image)
    return f"/media/post_images/{filename}"


async def save_profile_image(upload: UploadFile) -> str:
    """支持资料弹窗中点击头像后立即上传并替换头像文件。

    校验和磁盘写入规则与正文图片一致，但只返回随机文件名，因为用户表保存 ``image_file``，
    公开 URL 由 User 属性统一生成。Router 随后调用 ``set_profile_image`` 绑定到当前用户；
    本函数不改数据库，防止图片处理层承担用户权限与资料更新职责。
    """

    content = await upload.read(MAX_IMAGE_BYTES + 1)
    if len(content) > MAX_IMAGE_BYTES:
        raise InvalidImageError("Image must not exceed 5 MB")
    extension = _detect_extension(content)
    if extension is None:
        raise InvalidImageError("Only PNG, JPEG, GIF and WebP images are allowed")

    filename = f"{uuid4().hex}{extension}"
    target = PROFILE_IMAGE_DIR / filename

    def write_image() -> None:
        """在线程中完成目录创建和头像写盘，避免阻塞异步请求处理。"""

        PROFILE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    await to_thread.run_sync(write_image)
    return filename


async def delete_profile_image(filename: str | None) -> bool:
    """删除由本站生成的头像文件；非法路径和不存在文件按幂等成功处理。

    数据库只应保存随机文件名，但删除仍验证 ``Path.name``，避免历史脏数据把清理范围带出
    ``profile_pics``。删除失败只记录诊断信息并返回 False：数据库提交后不能因为旧文件
    清理失败把一次已成功的头像更新伪装成失败响应，残留文件可再由运维任务清理。
    """

    if not filename or Path(filename).name != filename:
        return True
    target = PROFILE_IMAGE_DIR / filename

    def unlink_image() -> bool:
        """在线程中执行同步 unlink，并让重复清理保持幂等。"""

        try:
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Could not delete profile image",
                extra={"image_filename": filename},
                exc_info=True,
            )
            return False
        return True

    return await to_thread.run_sync(unlink_image)
