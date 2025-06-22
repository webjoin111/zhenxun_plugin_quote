import os
from pathlib import Path

from zhenxun.configs.config import Config
from zhenxun.configs.path_config import DATA_PATH, FONT_PATH
from zhenxun.services.log import logger


def get_quote_path() -> Path:
    """获取语录图片保存路径，优先使用配置，否则使用默认路径"""
    custom_path = Config.get_config("quote", "QUOTE_PATH", "")

    if custom_path:
        custom_path = normalize_path(custom_path)
        logger.info(f"使用自定义语录路径: {custom_path}", "群聊语录")
        return custom_path
    else:
        default_path = DATA_PATH / "quote" / "images"
        logger.debug(f"使用默认语录路径: {default_path}", "群聊语录")
        return default_path


def ensure_quote_path() -> Path:
    """确保语录图片目录存在并返回路径"""
    quote_path = get_quote_path()
    quote_path.mkdir(parents=True, exist_ok=True)
    return quote_path


def get_quote_image_path(filename: str) -> Path:
    """获取语录图片的完整路径"""
    quote_path = ensure_quote_path()
    return quote_path / filename


def normalize_path(path: str | Path) -> Path:
    """标准化路径，确保跨平台兼容性"""
    if isinstance(path, str):
        return Path(path)
    return path


def safe_file_exists(file_path: str | Path) -> bool:
    """安全地检查文件是否存在，处理权限和路径问题"""
    try:
        path = normalize_path(file_path)
        return path.exists() and path.is_file()
    except (OSError, PermissionError) as e:
        logger.warning(f"检查文件存在性时出错: {file_path}, 错误: {e}", "群聊语录")
        return False


def ensure_directory_exists(dir_path: str | Path) -> Path:
    """确保目录存在，如果不存在则创建"""
    try:
        path = normalize_path(dir_path)
        path.mkdir(parents=True, exist_ok=True)
        return path
    except (OSError, PermissionError) as e:
        logger.error(f"创建目录失败: {dir_path}, 错误: {e}", "群聊语录")
        raise


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
