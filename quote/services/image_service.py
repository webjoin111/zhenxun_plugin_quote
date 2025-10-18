import asyncio
from pathlib import Path
import hashlib
from concurrent.futures import ThreadPoolExecutor

from PIL import Image
import aiofiles

from zhenxun.services.log import logger

from ..utils.exceptions import ImageProcessError


class ImageService:
    """图片服务类"""

    _executor = ThreadPoolExecutor()

    @staticmethod
    async def save_image(
        image_data: bytes, save_dir: str | Path, prefix: str = ""
    ) -> str:
        """保存图片到指定目录"""
        try:
            image_hash = hashlib.md5(image_data).hexdigest()
            if prefix:
                filename = f"{prefix}_{image_hash}.png"
            else:
                filename = f"{image_hash}.png"

            save_dir_path = Path(save_dir)
            save_dir_path.mkdir(parents=True, exist_ok=True)
            image_path = save_dir_path / filename

            async with aiofiles.open(image_path, "wb") as file:
                await file.write(image_data)

            logger.info(f"图片已保存到: {image_path}", "群聊语录")
            return str(image_path)
        except Exception as e:
            logger.error(f"保存图片失败: {e}", "群聊语录", e=e)
            raise ImageProcessError(f"保存图片失败: {e}")

    @staticmethod
    async def verify_image(image_path: str) -> bool:
        """验证图片是否有效"""
        try:

            def _verify():
                try:
                    with Image.open(image_path) as img:
                        img.verify()
                    return True
                except Exception as inner_e:
                    logger.warning(
                        f"图片验证失败: {image_path}, 错误: {inner_e}",
                        "群聊语录",
                        e=inner_e,
                    )
                    return False

            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(ImageService._executor, _verify)
            return result
        except Exception as e:
            logger.warning(
                f"图片验证过程发生异常: {image_path}, 错误: {e}", "群聊语录", e=e
            )
            return False
