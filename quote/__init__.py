from nonebot import get_driver
from nonebot.plugin import PluginMetadata

from zhenxun.configs.utils import PluginExtraData, RegisterConfig
from zhenxun.services.log import logger
from .command.manage_commands import (  # noqa: F401
    addtag_cmd,
    delete_by_keyword_cmd,
    delete_record,
    deltag_cmd,
)
from .command.query_commands import (  # noqa: F401
    alltag_cmd,
    quote_stats_cmd,
    record_pool,
)
from .command.upload_commands import (  # noqa: F401
    copy_batch_cmd,
    make_record_cmd,
    render_quote_cmd,
    save_img_cmd,
    script_batch_cmd,
)
from .config import ensure_quote_path

ensure_quote_path()
driver = get_driver()


@driver.on_startup
async def init_services():
    """初始化"""

    try:
        from .services.ai_service import AIService

        await AIService.initialize()
        logger.info("AI服务初始化完成", "群聊语录")
    except Exception as e:
        logger.error(f"AI服务初始化失败: {e}", "群聊语录", e=e)

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
        from .services.ai_service import AIService

        AIService.shutdown()
        logger.info("AI服务已关闭", "群聊语录")
    except Exception as e:
        logger.error(f"AI服务关闭失败: {e}", "群聊语录", e=e)

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
    【基础命令】
    - 语录：随机发送一条语录
    - 语录 [关键词]：搜索并发送包含关键词的语录
    - 语录 @用户：随机发送该用户的语录
    - 语录 @用户 [关键词]：搜索并发送该用户包含关键词的语录
    - 上传 [图片]：上传图片作为语录
    - 上传 [回复消息]：将回复的消息上传为语录
    - 记录 [回复消息]：将回复的文本消息生成为语录并保存
    - 生成 [回复消息]：将回复的文本消息生成为语录但不保存

    【标签命令】
    - 标签/tag [回复语录]：查看语录的所有标签
    - addtag [标签1] [标签2] [回复语录]：为语录添加标签
    - deltag [标签1] [标签2] [回复语录]：删除语录的标签

    【管理命令】
    - 删除 [回复语录]：删除回复的语录
    - 删除关键词 [关键词]：删除包含关键词的语录
    - 删除关键词 @用户 [关键词]：删除该用户包含关键词的语录

    【统计命令】
    - 语录统计 热门：显示群内热门语录排行
    - 语录统计 高产上传：显示上传语录最多的用户排行
    - 语录统计 高产被录：显示被记录语录最多的用户排行
    """,
    type="application",
    homepage="https://github.com/webjoin111/zhenxun_plugin_quote",
    supported_adapters={"~onebot.v11"},
    extra=PluginExtraData(
        author="webjoin111",
        version="v0.4.4",
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
                value=False,
                help="是否使用GPU加速OCR识别",
                default_value=False,
            ),
            RegisterConfig(
                module="quote",
                key="AI_ENABLED",
                value=False,
                help="是否启用AI识别功能（启用后会先尝试使用AI识别，失败则降级使用OCR）",
                default_value=False,
            ),
            RegisterConfig(
                module="quote",
                key="AI_CONFIG",
                value={
                    "api_key": "AIzaSyARl-rHUKVn4ZuhmZcY1J0gE******blZf8",
                    "model_name": "gemini-2.0-flash-exp",
                    "api_base": "https://generativelanguage.googleapis.com/v1beta",
                },
                help="AI配置（包含API密钥、模型名称和API基础URL）",
                default_value={
                    "api_key": "",
                    "model_name": "gemini-2.0-flash-exp",
                    "api_base": "https://generativelanguage.googleapis.com/v1beta",
                },
            ),
            RegisterConfig(
                module="quote",
                key="FONT_NAME",
                value="",
                help="正文字体名称（位于FONT_PATH目录下，留空则使用第一个可用字体）",
                default_value="",
            ),
            RegisterConfig(
                module="quote",
                key="AUTHOR_FONT_NAME",
                value="",
                help="作者字体名称（位于FONT_PATH目录下，留空则使用第一个可用字体）",
                default_value="",
            ),
            RegisterConfig(
                module="quote",
                key="QUOTE_PATH",
                value="",
                help="语录图片保存路径（留空则使用默认路径：DATA_PATH/quote/images）",
                default_value="",
            ),
        ],
    ).dict(),
)


try:
    import sys
    from zhenxun.services.db_context import MODELS

    current_module = sys.modules[__name__]
    model_path = f"{current_module.__package__}.model"

    if model_path not in MODELS:
        MODELS.append(model_path)
        logger.info(f"Quote 模型已添加到 MODELS 列表: {model_path}", "群聊语录")
except ImportError:
    logger.error("无法导入 zhenxun.services.db_context，Quote 模型注册失败", "群聊语录")
except Exception as e:
    logger.error(f"注册 Quote 模型失败: {e}", "群聊语录", e=e)
