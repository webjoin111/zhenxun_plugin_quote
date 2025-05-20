import hashlib
import io
import os

import aiofiles
import imagehash
from PIL import Image

from zhenxun.services.log import logger
from zhenxun.utils.http_utils import AsyncHttpx
from zhenxun.utils.platform import PlatformUtils

from .exceptions import NetworkError

IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif"]
DEFAULT_AVATAR_MD5 = "acef72340ac0e914090bd35799f5594e"


async def download_qq_avatar(qqid: str | int) -> bytes:
    """下载QQ头像"""
    qqid_str = str(qqid)

    try:
        logger.info(f"开始获取QQ头像: {qqid_str}", "群聊语录")
        data = await PlatformUtils.get_user_avatar(qqid_str, "qq")
        if not data:
            logger.warning(f"获取QQ头像失败: {qqid_str}", "群聊语录")
            raise NetworkError("获取头像失败")

        if hashlib.md5(data).hexdigest() == DEFAULT_AVATAR_MD5:
            logger.info(f"检测到默认头像，尝试使用较小尺寸: {qqid_str}", "群聊语录")
            url = f"http://q1.qlogo.cn/g?b=qq&nk={qqid_str}&s=100"
            data = await AsyncHttpx.get_content(url)
            if not data:
                logger.warning(f"获取较小尺寸头像失败: {qqid_str}", "群聊语录")
                raise NetworkError("获取头像失败")
        logger.info(f"QQ头像获取成功: {qqid_str}", "群聊语录")
        return data
    except Exception as e:
        logger.error(f"下载QQ头像失败: {e}", "群聊语录", e=e)
        raise NetworkError(f"下载QQ头像失败: {e}")


async def save_image_from_url(url: str, save_path: str) -> str:
    """下载并保存图片"""
    try:
        logger.info(f"开始下载图片: {url}", "群聊语录")
        content = await AsyncHttpx.get_content(url)
        if content:
            random_filename = f"{hashlib.md5(url.encode()).hexdigest()}.png"
            image_path = os.path.join(save_path, random_filename)
            async with aiofiles.open(image_path, "wb") as f:
                await f.write(content)
            logger.info(f"图片下载成功，保存至: {image_path}", "群聊语录")
            return image_path
        else:
            logger.warning(f"下载图片失败: {url}", "群聊语录")
            raise NetworkError("下载失败，未获取到内容")
    except Exception as e:
        logger.error(f"下载或保存图片失败: {e}", "群聊语录", e=e)
        raise NetworkError(f"下载或保存图片失败: {e}")


async def get_img_md5(img_path: str) -> str:
    """计算图片MD5"""
    async with aiofiles.open(img_path, "rb") as f:
        img_data = await f.read()
    md5 = hashlib.md5(img_data).hexdigest()
    return md5


async def get_img_hash(img_path: str) -> str:
    """计算图片的感知哈希值"""
    try:
        logger.info(f"计算图片哈希值: {img_path}", "群聊语录")
        async with aiofiles.open(img_path, "rb") as f:
            img_data = await f.read()

        # 使用BytesIO避免再次写入文件
        img = Image.open(io.BytesIO(img_data))

        # 计算感知哈希值
        phash = str(imagehash.phash(img))
        logger.info(f"图片哈希值计算成功: {phash}", "群聊语录")
        return phash
    except Exception as e:
        logger.error(f"计算图片哈希值失败: {e}", "群聊语录", e=e)
        # 如果计算失败，返回空字符串
        return ""


async def get_img_hash_from_bytes(img_data: bytes) -> str:
    """从字节数据计算图片哈希值"""
    try:
        logger.info("计算图片字节数据的哈希值", "群聊语录")

        # 使用BytesIO避免写入文件
        img = Image.open(io.BytesIO(img_data))

        # 计算感知哈希值
        phash = str(imagehash.phash(img))
        logger.info(f"图片哈希值计算成功: {phash}", "群聊语录")
        return phash
    except Exception as e:
        logger.error(f"计算图片哈希值失败: {e}", "群聊语录", e=e)
        # 如果计算失败，返回空字符串
        return ""


async def copy_images_files(source: str, destinate: str) -> list[tuple[str, str]]:
    """复制图片文件"""

    image_files = []
    for root, _, files in os.walk(source):
        for filename in files:
            extension = os.path.splitext(filename)[1].lower()
            if extension in IMAGE_EXTENSIONS:
                image_path = os.path.join(root, filename)
                md5 = await get_img_md5(image_path) + ".image"
                tname = md5 + extension
                destination_path = os.path.join(destinate, tname)
                async with aiofiles.open(image_path, "rb") as src:
                    content = await src.read()
                async with aiofiles.open(destination_path, "wb") as dst:
                    await dst.write(content)
                image_files.append((md5, tname))
    return image_files



