from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from .command.manage_commands import ( # noqa: F401
    addtag_cmd,  
    adv_delete_cmd,
    delete_record,
    deltag_cmd,
)
from .command.query_commands import ( # noqa: F401
    alltag_cmd,
    quote_stats_cmd,
    record_pool,
)
from .command.upload_commands import ( # noqa: F401
    generate_quote_cmd,
    make_record_cmd,
    save_img_cmd,
)
from .config import ensure_quote_path

ensure_quote_path()
driver = get_driver()


@driver.on_startup
async def init_services():
    """初始化"""

    try:
        from .services.ocr_service import OCRService

        await OCRService.initialize_engine()
        logger.info("OCR服务初始化完成", "群聊语录")
    except Exception as e:
        logger.error(f"OCR服务初始化失败: {e}", "群聊语录", e=e)


@driver.on_shutdown
async def shutdown_services():
    """关闭"""
    try:
        from .services.ocr_service import OCRService

        OCRService.shutdown()
        logger.info("OCR服务已关闭", "群聊语录")
    except Exception as e:
        logger.error(f"OCR服务关闭失败: {e}", "群聊语录", e=e)


__plugin_meta__ = PluginMetadata(
    name="群聊语录",
    description="一款QQ群语录库——支持上传聊天截图为语录，随机投放语录，关键词搜索语录精准投放",
    usage="""
    📝 **核心功能 (所有用户)**
    - `语录`：随机发送一条语录。
    - `语录 [关键词]`：搜索包含指定关键词的语录。
    - `语录 [词1] [词2]`：搜索同时包含所有关键词的语录 (AND逻辑)。
    - `语录 @用户`：随机发送指定用户的语录。
    - `语录 @用户 [关键词]`：搜索指定用户包含关键词的语录。
    - `上传 [图片]`：上传图片作为语录。
    - `上传` (回复图片消息)：将回复的图片上传为语录。
    - `记录` (回复文本消息)：将回复的文本生成语录图片并保存。

    🎨 **主题与生成**
    - `语录主题`：查看所有可用的语录卡片主题。
    - `记录 -s [主题ID]`：在记录时使用指定主题生成图片。
    - `生成` (回复文本消息)：预览生成的语录图片，但不会保存。
    - `生成 -s [主题ID]`：使用指定主题进行预览。

    🏷️ **标签管理 (管理员权限)**
    - `标签` / `tag` (回复语录)：查看该语录的所有标签。
    - `addtag [标签1] [标签2]...` (回复语录)：为语录添加一个或多个标签。
    - `deltag [标签1] [标签2]...` (回复语录)：删除语录的一个或多个标签。

    📊 **语录统计**
    - `语录统计 热门 [数量]`：显示群内热门语录排行 (默认10条)。
    - `语录统计 高产上传 [数量]`：显示上传语录最多的用户排行。
    - `语录统计 高产被录 [数量]`：显示被记录语录最多的用户排行。
    > 超级用户可使用 `-g [群号]` 在任意位置查询指定群的统计。

    🛠️ **基础管理 (管理员权限)**
    - `删除` (回复语录)：删除回复的语录图片及其记录。

    ⚙️ **高级管理 (超级用户权限)**
    - `语录管理 删除关键词 [词1] [词2]...`
      > 删除包含任一关键词的语录 (OR逻辑)。
      > 可附加 `--uploader [@/QQ号]` 或 `--quoted [@/QQ号]` 进行筛选。
    - `语录管理 清空全部 --uploader [@/QQ号]`
      > 删除指定上传者的所有语录。
    - `语录管理 清空全部 --quoted [@/QQ号]`
      > 删除被记录的指定用户的所有语录。
    - `语录管理 清空全部 --group [群号]`
      > **[高危]** 删除指定群号的所有语录。
    - `语录管理 清理 退群用户`
      > 自动清理所有已退群用户的相关语录。
    - `语录管理 ... -g [群号]`
      > 在任意位置对指定群聊执行上述高级管理操作。
    """,
    type="application",
    homepage="https://github.com/webjoin111/zhenxun_plugin_quote",
    supported_adapters={"~onebot.v11"},
    extra=PluginExtraData(
        author="webjoin111",
        version="v1.0.0",
        admin_level=0,
        configs=[
            RegisterConfig(
                module="quote",
                key="OCR_ENGINE",
                value="easyocr",
                help="OCR引擎选择，可选值: easyocr, paddleocr",
                default_value="easyocr",
            ),
            RegisterConfig(
                module="quote",
                key="OCR_USE_GPU",
                value=True,
                help="是否使用GPU加速OCR识别",
                default_value=True,
            ),
            RegisterConfig(
                module="quote",
                key="AI_ENABLED",
                value=True,
                help="是否启用AI识别功能（启用后会先尝试使用AI识别，失败则降级使用OCR）",
                default_value=True,
            ),
            RegisterConfig(
                module="quote",
                key="OCR_AI_MODEL",
                value="Gemini/gemini-2.5-flash-lite-preview-06-17",
                help="用于OCR的、支持视觉功能的AI模型全名 (格式: Provider/ModelName)",
                default_value="Gemini/gemini-2.5-flash-lite-preview-06-17",
            ),
            RegisterConfig(
                module="quote",
                key="DEFAULT_TEXT_FONT",
                value="SarasaFixedSC-Regular.ttf",
                help="默认正文字体文件名（需放置在 quote/assets/fonts/ 目录下）。",
                default_value="SarasaFixedSC-Regular.ttf",
            ),
            RegisterConfig(
                module="quote",
                key="DEFAULT_AUTHOR_FONT",
                value="SarasaFixedSC-Regular.ttf",
                help="默认作者字体文件名（留空则使用正文字体）。",
                default_value="SarasaFixedSC-Regular.ttf",
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_PATH",
                value="",
                help="语录图片保存路径（留空则使用默认路径：DATA_PATH/quote/images）",
                default_value="",
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_THEME",
                value=["classic"],
                help="生成语录卡片时使用的主题列表，会从中随机选择。填入 'all' 则在所有可用主题中随机。",
                default_value=["classic"],
            ),
            RegisterConfig(
                module="quote",
                key="DELETE_ADMIN_LEVEL",
                value=1,
                help="设置使用「删除」命令所需的权限等级。默认值为1，允许群管理员使用。",
                default_value=1,
            ),
        ],
    ).dict(),
)


try:
    from zhenxun.services.cache import CacheRegistry
    from .model import Quote, QUOTE_CACHE_TYPE

    CacheRegistry.register(QUOTE_CACHE_TYPE, Quote)
    logger.info(f"Quote 插件缓存类型 ({QUOTE_CACHE_TYPE}) 注册成功", "群聊语录")

except ImportError:
    logger.error("无法导入 zhenxun.services.cache，Quote 插件缓存注册失败", "群聊语录")
except Exception as e:
    logger.error(f"注册 Quote 插件缓存失败: {e}", "群聊语录", e=e)