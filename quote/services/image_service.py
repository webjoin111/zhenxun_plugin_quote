import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
from pathlib import Path
import textwrap

import aiofiles
from PIL import Image, ImageDraw, ImageFont

from zhenxun.services.log import logger

from ..config import get_author_font_path, get_font_path
from ..pilmoji import Pilmoji
from ..pilmoji.source import RobustGoogleEmojiSource
from ..utils.exceptions import ImageProcessError


class ImageService:
    """图片服务类"""

    _executor = ThreadPoolExecutor()

    @staticmethod
    def _make_square(image, size):
        """将图片裁剪为正方形并调整大小"""
        width, height = image.size
        new_size = min(width, height)
        left = (width - new_size) // 2
        top = (height - new_size) // 2
        right = (width + new_size) // 2
        bottom = (height + new_size) // 2
        cropped_image = image.crop((left, top, right, bottom))
        return cropped_image.resize((size, size), Image.LANCZOS)

    @staticmethod
    def _create_gradient(size):
        """创建渐变效果"""
        gradient = Image.new("RGBA", size)
        draw = ImageDraw.Draw(gradient)
        for x in range(size[0]):
            alpha = int(255 * (1 - (1 - x / size[0])) ** 2)
            draw.line((x, 0, x, size[1]), fill=(0, 0, 0, alpha))
        return gradient

    @staticmethod
    def _generate_quote_image(avatar_bytes, text, author, font_path, author_font_path):
        """内部生成图片"""

        def transbox(bbox):
            return bbox[2] - bbox[0], bbox[3] - bbox[1]

        text = "　　「" + text + "」"
        fixed_height = 400
        canvas_width = fixed_height * 3

        avatar_size = fixed_height
        avatar = Image.open(avatar_bytes)
        avatar = ImageService._make_square(avatar, avatar_size)

        canvas = Image.new("RGBA", (canvas_width, fixed_height), (0, 0, 0, 0))
        canvas.paste(avatar, (0, 0))

        gradient = ImageService._create_gradient((avatar_size, fixed_height))
        canvas.paste(gradient, (0, 0), gradient)

        text_area_width = canvas_width - avatar_size
        text_area_height = fixed_height
        text_area = Image.new(
            "RGBA", (text_area_width, text_area_height), (0, 0, 0, 255)
        )

        text_length = len(text)
        if text_length <= 20:
            font_size = 80
        elif text_length <= 50:
            font_size = 70
        elif text_length <= 100:
            font_size = 60
        elif text_length <= 150:
            font_size = 55
        elif text_length <= 200:
            font_size = 50
        elif text_length <= 300:
            font_size = 45
        elif text_length <= 400:
            font_size = 40
        elif text_length <= 500:
            font_size = 35
        elif text_length <= 700:
            font_size = 30
        elif text_length <= 1000:
            font_size = 25
        else:
            font_size = 20

        font = ImageFont.truetype(font_path, font_size)

        max_text_width = text_area_width - 40
        max_text_height = text_area_height - 80
        line_spacing = 10

        wrapped_text = []

        if text:
            wrap_width = max(15, min(30, int(50 / (font_size / 40))))

            wrapped_lines = textwrap.wrap(text, width=wrap_width, drop_whitespace=False)
            lines = []
            current_line = []
            for word in wrapped_lines:
                current_line.append(word)
                if transbox(font.getbbox("".join(current_line)))[0] >= max_text_width:
                    lines.append("".join(current_line[:-1]))
                    current_line = [current_line[-1]]
            if current_line:
                lines.append("".join(current_line))
            wrapped_text = lines

            while True:
                current_width = (
                    max(transbox(font.getbbox(line))[0] for line in wrapped_text)
                    if wrapped_text
                    else 0
                )

                line_height = transbox(font.getbbox("A"))[1]
                current_height = len(wrapped_text) * line_height + (
                    (len(wrapped_text) - 1) * line_spacing
                )

                if (
                    current_width <= max_text_width * 0.98
                    and current_height <= max_text_height * 0.95
                ):
                    break

                if font_size > 40:
                    font_size -= 3
                elif font_size > 25:
                    font_size -= 2
                else:
                    font_size -= 1

                if font_size < 12:
                    font_size = 12

                font = ImageFont.truetype(font_path, font_size)

                wrap_width = max(15, min(30, int(50 / (font_size / 40))))

                wrapped_lines = textwrap.wrap(
                    text, width=wrap_width, drop_whitespace=False
                )
                lines = []
                current_line = []
                for word in wrapped_lines:
                    current_line.append(word)
                    if (
                        transbox(font.getbbox("".join(current_line)))[0]
                        >= max_text_width
                    ):
                        lines.append("".join(current_line[:-1]))
                        current_line = [current_line[-1]]
                if current_line:
                    lines.append("".join(current_line))
                wrapped_text = lines

                if font_size <= 12:
                    break

        quote_content = "\n".join(wrapped_text)

        y = 0
        lines = quote_content.split("\n")
        line_height = transbox(font.getbbox("A"))[1]

        if len(lines) == 1:
            lines[0] = lines[0][2:]

        total_content_height = len(lines) * line_height + (
            (len(lines) - 1) * line_spacing
        )

        vertical_offset = (text_area_height - total_content_height) // 2 - 30

        total_text_width = max(transbox(font.getbbox(line))[0] for line in lines)
        left_offset = (text_area_width - total_text_width) // 2 - 20

        emoji_source = RobustGoogleEmojiSource(disk_cache=True, enable_fallback=True)

        for line in lines:
            x = left_offset + 20
            try:
                with Pilmoji(text_area, source=emoji_source) as pilmoji:
                    pilmoji.text(
                        (x, vertical_offset + y),
                        line,
                        font=font,
                        fill=(255, 255, 255, 255),
                    )
            except Exception as e:
                logger.warning(f"表情符号渲染失败，使用普通文本: {e}", "群聊语录")
                draw = ImageDraw.Draw(text_area)
                draw.text(
                    (x, vertical_offset + y), line, font=font, fill=(255, 255, 255, 255)
                )
            y += line_height + line_spacing

        author_font = ImageFont.truetype(author_font_path, 40)
        author_text = "— " + author
        author_width = transbox(author_font.getbbox(author_text))[0]
        author_x = text_area_width - author_width - 40
        author_y = text_area_height - transbox(author_font.getbbox("A"))[1] - 40
        try:
            with Pilmoji(text_area, source=emoji_source) as pilmoji:
                pilmoji.text(
                    (author_x, author_y),
                    author_text,
                    font=author_font,
                    fill=(255, 255, 255, 255),
                )
        except Exception as e:
            logger.warning(f"作者名表情符号渲染失败，使用普通文本: {e}", "群聊语录")
            draw = ImageDraw.Draw(text_area)
            draw.text(
                (author_x, author_y),
                author_text,
                font=author_font,
                fill=(255, 255, 255, 255),
            )

        canvas.paste(text_area, (avatar_size, 0))

        img_byte_arr = io.BytesIO()
        canvas.save(img_byte_arr, format="PNG")
        img_byte_arr = img_byte_arr.getvalue()

        return img_byte_arr

    @staticmethod
    async def generate_quote(
        avatar_bytes: bytes,
        text: str,
        author: str,
        font_path: str = None,
        author_font_path: str = None,
    ) -> bytes:
        """生成语录图片"""
        try:
            logger.info(
                f"开始生成语录图片 - 作者: {author}, 文本长度: {len(text)}", "群聊语录"
            )

            if font_path is None:
                font_path = get_font_path()
            if author_font_path is None:
                author_font_path = get_author_font_path()

            logger.debug(
                f"使用字体 - 正文: {font_path}, 作者: {author_font_path}", "群聊语录"
            )

            image_file = io.BytesIO(avatar_bytes)
            loop = asyncio.get_running_loop()
            img_data = await loop.run_in_executor(
                ImageService._executor,
                lambda: ImageService._generate_quote_image(
                    image_file, text, author, font_path, author_font_path
                ),
            )

            logger.info("语录图片生成成功", "群聊语录")
            return img_data
        except Exception as e:
            logger.error(f"生成语录图片失败: {e}", "群聊语录", e=e)
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
