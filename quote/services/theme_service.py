import json
from pathlib import Path
from copy import deepcopy

from pydantic import BaseModel, Field

from zhenxun.services.log import logger
from zhenxun.configs.config import Config
from zhenxun.configs.path_config import FONT_PATH as GLOBAL_FONT_PATH

THEMES_PATH = Path(__file__).parent.parent / "assets" / "themes"
PLUGIN_FONTS_PATH = Path(__file__).parent.parent / "assets" / "fonts"
FALLBACK_FONT_FILE = "SarasaFixedSC-Regular.ttf"


class ThemeAssets(BaseModel):
    template: str | None = None
    style: str | None = None
    text_font_file: str | None = None
    author_font_file: str | None = None


class ThemeViewport(BaseModel):
    width: int
    height: int


class ThemeConfig(BaseModel):
    """主题元数据模型"""

    name: str
    extends: str | None = None
    description: str | None = None
    author: str | None = None
    viewport: ThemeViewport | None = None
    assets: ThemeAssets | None = None
    palette: dict[str, str] = Field(default_factory=dict)
    directory: Path = Field(default=Path(), exclude=True)


class ResolvedThemeData(BaseModel):
    """一个主题被完整解析后的数据，包含所有绝对路径和配置"""

    template_path: Path
    style_path: Path
    text_font_path: Path | None = None
    author_font_path: Path | None = None
    viewport: ThemeViewport
    palette: dict[str, str]

    class Config:
        arbitrary_types_allowed = True


class ThemeService:
    """主题管理服务"""

    def __init__(self):
        self._themes: dict[str, ThemeConfig] = {}
        self._resolved_themes: dict[str, ResolvedThemeData] = {}
        self._resolved_themes_config_snapshot: dict[str, dict[str, str | None]] = {}
        self._load_themes()

    def _find_font(self, filename: str | None) -> Path | None:
        """
        按优先级在多个位置查找字体文件，并始终返回绝对路径。
        优先级: 1. 插件内置字体库 -> 2. 真寻全局字体库
        """
        if not filename:
            return None

        plugin_font_path = PLUGIN_FONTS_PATH / filename
        if plugin_font_path.exists():
            return plugin_font_path.resolve()

        global_font_path = GLOBAL_FONT_PATH / filename
        if global_font_path.exists():
            return global_font_path.resolve()

        logger.warning(f"在插件字体库和全局字体库中均未找到字体文件: {filename}")
        return None

    def _load_themes(self):
        """扫描并加载所有主题"""
        if not THEMES_PATH.is_dir():
            logger.warning("主题目录不存在", "ThemeService")
            return

        for theme_dir in THEMES_PATH.iterdir():
            if theme_dir.is_dir():
                theme_id = theme_dir.name
                config_path = theme_dir / "theme.json"
                if config_path.is_file():
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            config_data = json.load(f)
                        theme_config = ThemeConfig(**config_data)
                        theme_config.directory = theme_dir
                        self._themes[theme_id] = theme_config
                        logger.info(
                            f"已加载主题: {theme_id} ({theme_config.name})",
                            "ThemeService",
                        )
                    except Exception as e:
                        logger.error(
                            f"加载主题 '{theme_id}' 失败: {e}", "ThemeService", e=e
                        )
                else:
                    logger.warning(
                        f"主题 '{theme_id}' 缺少 theme.json 文件", "ThemeService"
                    )

    def get_theme(self, name: str) -> ResolvedThemeData:
        """获取并完整解析一个主题，处理继承关系并返回包含绝对路径的解析数据"""

        global_text_font_file = Config.get_config(
            "quote", "DEFAULT_TEXT_FONT", FALLBACK_FONT_FILE
        )
        global_author_font_file = Config.get_config("quote", "DEFAULT_AUTHOR_FONT")
        current_config_snapshot = {
            "text": global_text_font_file,
            "author": global_author_font_file,
        }

        if name in self._resolved_themes:
            cached_config = self._resolved_themes_config_snapshot.get(name)
            if cached_config == current_config_snapshot:
                return self._resolved_themes[name]
            else:
                logger.info(f"检测到字体配置变更，正在为主题 '{name}' 重新加载。")
                del self._resolved_themes[name]
                if name in self._resolved_themes_config_snapshot:
                    del self._resolved_themes_config_snapshot[name]

        if name not in self._themes:
            raise ValueError(f"主题 '{name}' 不存在")

        fallback_font_path = PLUGIN_FONTS_PATH / FALLBACK_FONT_FILE
        global_text_font_path = (
            self._find_font(global_text_font_file) or fallback_font_path
        )
        global_author_font_path = (
            self._find_font(global_author_font_file) or global_text_font_path
        )
        logger.debug(
            f"正文字体路径: {global_text_font_path}\n 作者字体路径: {global_author_font_path}"
        )

        config = self._themes[name]
        if config.extends:
            parent_data = self.get_theme(config.extends)
            resolved_data = deepcopy(parent_data)

            if config.assets:
                if config.assets.template:
                    resolved_data.template_path = (
                        config.directory / config.assets.template
                    )
                if config.assets.style:
                    resolved_data.style_path = config.directory / config.assets.style
                if config.assets.text_font_file:
                    resolved_data.text_font_path = self._find_font(
                        config.assets.text_font_file
                    )
                if config.assets.author_font_file:
                    resolved_data.author_font_path = self._find_font(
                        config.assets.author_font_file
                    )

            if config.viewport:
                resolved_data.viewport = config.viewport

            resolved_data.palette.update(config.palette)

        else:
            if not config.assets or not config.viewport:
                raise ValueError(
                    f"基础主题 '{name}' 缺少必需的 'assets' 或 'viewport' 配置。"
                )

            text_font_path = (
                self._find_font(config.assets.text_font_file) or global_text_font_path
            )
            author_font_path = (
                self._find_font(config.assets.author_font_file)
                or global_author_font_path
            )

            resolved_data = ResolvedThemeData(
                template_path=config.directory / config.assets.template,
                style_path=config.directory / config.assets.style,
                text_font_path=text_font_path,
                author_font_path=author_font_path,
                viewport=config.viewport,
                palette=config.palette,
            )

        if not resolved_data.text_font_path:
            resolved_data.text_font_path = global_text_font_path
        if not resolved_data.author_font_path:
            resolved_data.author_font_path = resolved_data.text_font_path

        self._resolved_themes[name] = resolved_data
        self._resolved_themes_config_snapshot[name] = current_config_snapshot
        return resolved_data

    def list_themes(self) -> list[dict[str, str]]:
        """列出所有可用主题的基本信息"""
        return [
            {
                "id": theme_id,
                "name": theme.name,
                "description": theme.description or "无描述",
            }
            for theme_id, theme in self._themes.items()
        ]


theme_service = ThemeService()
