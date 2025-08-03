import asyncio
import base64
from pathlib import Path
import hashlib
from concurrent.futures import ThreadPoolExecutor

import aiofiles
from nonebot_plugin_htmlrender import template_to_pic
from nonebot.compat import model_dump
from PIL import Image

from zhenxun.services.log import logger

from ..utils.exceptions import ImageProcessError
from .theme_service import theme_service


class ImageService:
    """图片服务类"""

    _executor = ThreadPoolExecutor()

    @staticmethod
    async def generate_quote(
        avatar_bytes: bytes,
        text: str,
        author: str,
        style_name: str,
    ) -> bytes:
        """使用HTML模板生成语录图片"""
        try:
            logger.info(
                f"开始使用主题 '{style_name}' 生成语录图片 - 作者: {author}", "群聊语录"
            )

            theme_data = theme_service.get_theme(style_name)

            avatar_base64 = base64.b64encode(avatar_bytes).decode("utf-8")
            template_data = {
                "avatar_data_url": f"data:image/png;base64,{avatar_base64}",
                "text": text,
                "author": author,
                "palette": theme_data.palette,
                "style_path": theme_data.style_path.as_uri(),
                "text_font_face_src": theme_data.text_font_path.as_uri()
                if theme_data.text_font_path
                else None,
                "author_font_face_src": theme_data.author_font_path.as_uri()
                if theme_data.author_font_path
                else None,
            }

            img_data = await template_to_pic(
                template_path=str(theme_data.template_path.parent.resolve()),
                template_name=theme_data.template_path.name,
                templates=template_data,
                pages={
                    "viewport": model_dump(theme_data.viewport),
                    "base_url": theme_data.template_path.parent.as_uri(),
                },
                wait=0.2,
            )

            logger.info("HTML语录图片生成成功", "群聊语录")
            return img_data

        except Exception as e:
            logger.error(f"使用HTML模板生成语录图片失败: {e}", "群聊语录", e=e)
            raise ImageProcessError(f"生成语录图片失败: {e}")

    @staticmethod
    async def save_image(image_data: bytes, save_dir: str, prefix: str = "") -> str:
        """保存图片到指定目录"""
        try:
            image_hash = hashlib.md5(image_data).hexdigest()
            if prefix:
                filename = f"{prefix}_{image_hash}.png"
            else:
                filename = f"{image_hash}.png"

            save_dir = Path(save_dir)
            save_dir.mkdir(parents=True, exist_ok=True)
            image_path = save_dir / filename

            async with aiofiles.open(image_path, "wb") as file:
                await file.write(image_data)

            logger.info(f"图片已保存到: {image_path}", "群聊语录")
            return image_path
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
