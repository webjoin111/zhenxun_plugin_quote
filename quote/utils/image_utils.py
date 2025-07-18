import hashlib
import io
import os
from pathlib import Path

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
            logger.warning(f"获取QQ头像失败 (返回为空): {qqid_str}", "群聊语录")
            raise NetworkError("获取头像失败")
        
        
        logger.info(f"QQ头像获取成功: {qqid_str}", "群聊语录")
        return data
    except Exception as e:
        logger.error(f"下载QQ头像失败: {e}", "群聊语录", e=e)
        raise NetworkError(f"下载QQ头像失败: {e}")



async def save_image_from_url(url: str, save_path: str | Path) -> str:
    """下载并保存图片"""
    try:
        logger.info(f"开始下载图片: {url}", "群聊语录")

        random_filename = f"{hashlib.md5(url.encode()).hexdigest()}.png"
        save_path = Path(save_path)
        save_path.mkdir(parents=True, exist_ok=True)
        image_path = save_path / random_filename

        success = await AsyncHttpx.download_file(url, image_path)

        if success:
            logger.info(f"图片下载成功，保存至: {image_path}", "群聊语录")
            return str(image_path)
        else:
            logger.warning(f"下载图片失败 (download_file 返回 False): {url}", "群聊语录")
            raise NetworkError("下载失败，未获取到内容或保存失败")

    except Exception as e:
        logger.error(f"下载或保存图片失败: {e}", "群聊语录", e=e)
        raise NetworkError(f"下载或保存图片失败: {e}")


async def get_img_md5(img_path: str | Path) -> str:
    """计算图片MD5"""
    img_path = Path(img_path)
    if not img_path.exists():
        logger.error(f"图片文件不存在: {img_path}", "群聊语录")
        raise FileNotFoundError(f"图片文件不存在: {img_path}")

    async with aiofiles.open(img_path, "rb") as f:
        img_data = await f.read()
    md5 = hashlib.md5(img_data).hexdigest()
    return md5


async def _calculate_phash(img_data: bytes) -> str:
    """内部函数：从字节数据计算图片的感知哈希值"""
    try:
        img = Image.open(io.BytesIO(img_data))
        phash = str(imagehash.phash(img))
        logger.info(f"图片哈希值计算成功: {phash}", "群聊语录")
        return phash
    except Exception as e:
        logger.error(f"从字节流计算图片哈希值失败: {e}", "群聊语录", e=e)
        return ""


async def get_img_hash(img_path: str | Path) -> str:
    """计算图片的感知哈希值 (phash)"""
    img_path = Path(img_path)
    if not img_path.exists():
        logger.error(f"图片文件不存在: {img_path}", "群聊语录")
        return ""

    logger.info(f"正在读取图片文件以计算哈希: {img_path}", "群聊语录")
    try:
        async with aiofiles.open(img_path, "rb") as f:
            img_data = await f.read()
        return await _calculate_phash(img_data)
    except Exception as e:
        logger.error(f"读取图片文件失败: {e}", "群聊语录", e=e)
        return ""


async def get_img_hash_from_bytes(img_data: bytes) -> str:
    """从字节数据计算图片哈希值 (phash)"""
    logger.info("正在从字节数据计算图片哈希值", "群聊语录")
    return await _calculate_phash(img_data)


async def convert_image_to_png(image_path: str | Path) -> bytes:
    """将图片转换为PNG格式的字节数据"""
    try:
        image_path = Path(image_path)
        logger.info(f"转换图片为PNG格式: {image_path}", "群聊语录")

        async with aiofiles.open(image_path, "rb") as f:
            img_data = await f.read()

        img = Image.open(io.BytesIO(img_data))

        if img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        ):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")

        png_buffer = io.BytesIO()
        img.save(png_buffer, format="PNG")
        png_data = png_buffer.getvalue()

        logger.info(f"图片转换为PNG成功，大小: {len(png_data)} 字节", "群聊语录")
        return png_data
    except Exception as e:
        logger.error(f"转换图片为PNG失败: {e}", "群聊语录", e=e)
        raise


async def copy_images_files(
    source: str | Path, destinate: str | Path
) -> list[tuple[str, str]]:
    """复制图片文件，统一转换为PNG格式"""
    source = Path(source)
    destinate = Path(destinate)

    destinate.mkdir(parents=True, exist_ok=True)

    image_files = []
    for root, _, files in os.walk(source):
        root_path = Path(root)
        for filename in files:
            extension = Path(filename).suffix.lower()
            if extension in IMAGE_EXTENSIONS:
                image_path = root_path / filename
                if image_path.exists():
                    try:
                        png_data = await convert_image_to_png(image_path)

                        md5 = hashlib.md5(png_data).hexdigest() + ".image"

                        tname = md5 + ".png"
                        destination_path = destinate / tname

                        async with aiofiles.open(destination_path, "wb") as dst:
                            await dst.write(png_data)

                        image_files.append((md5, tname))
                        logger.info(
                            f"图片已转换并复制: {image_path} -> {destination_path}",
                            "群聊语录",
                        )
                    except Exception as e:
                        logger.error(
                            f"复制图片文件失败: {image_path} -> {destination_path}: {e}",
                            "群聊语录",
                            e=e,
                        )
                        continue
    return image_files
