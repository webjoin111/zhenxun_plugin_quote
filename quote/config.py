import os
from pathlib import Path

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH, FONT_PATH
from zhenxun.services.log import logger

quote_path = DATA_PATH / "quote" / "images"


def get_first_font_in_directory() -> Path | None:
    """获取第一个可用字体"""
    try:
        for file in os.listdir(FONT_PATH):
            if file.lower().endswith((".ttf", ".otf")):
                return FONT_PATH / file
    except Exception as e:
        logger.error(f"获取字体目录下的第一个字体文件失败: {e}", "群聊语录", e=e)
    return None


def get_font_path(font_name: str | None = None) -> Path:
    """获取正文字体路径，优先使用配置，否则使用第一个可用字体"""
    if not font_name:
        font_name = Config.get_config("quote", "FONT_NAME", "")

    if not font_name:
        first_font = get_first_font_in_directory()
        if first_font:
            return first_font
    else:
        font_in_dir = FONT_PATH / font_name
        if font_in_dir.exists():
            return font_in_dir

        first_font = get_first_font_in_directory()
        if first_font:
            logger.warning(
                f"找不到指定字体 '{font_name}'，使用默认字体 '{first_font.name}'",
                "群聊语录",
            )
            return first_font

    fallback_font = "font.ttf"
    logger.error(
        f"找不到任何字体，使用备用字体路径 '{FONT_PATH / fallback_font}'", "群聊语录"
    )
    return FONT_PATH / fallback_font


def get_author_font_path(author_font_name: str | None = None) -> Path:
    """获取作者字体路径，优先使用配置，否则使用第一个可用字体"""
    if not author_font_name:
        author_font_name = Config.get_config("quote", "AUTHOR_FONT_NAME", "")

    if not author_font_name:
        first_font = get_first_font_in_directory()
        if first_font:
            return first_font
    else:
        font_in_dir = FONT_PATH / author_font_name
        if font_in_dir.exists():
            return font_in_dir

        first_font = get_first_font_in_directory()
        if first_font:
            logger.warning(
                f"找不到指定作者字体 '{author_font_name}'，使用默认字体 '{first_font.name}'",
                "群聊语录",
            )
            return first_font

    fallback_font = "font.ttf"
    logger.error(
        f"找不到任何字体，使用备用作者字体路径 '{FONT_PATH / fallback_font}'",
        "群聊语录",
    )
    return FONT_PATH / fallback_font


def check_font(font_path: str | Path, author_font_path: str | Path) -> bool:
    """检查字体文件是否可用"""
    if not isinstance(font_path, Path):
        font_path = Path(font_path)
    if not isinstance(author_font_path, Path):
        author_font_path = Path(author_font_path)

    return font_path.exists() and author_font_path.exists()
