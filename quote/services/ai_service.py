from pathlib import Path

from pydantic import BaseModel, Field

from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.services.llm import LLMException, get_model_instance, generate_structured
from nonebot_plugin_alconna.uniseg import UniMessage, Text, Image


class OCRResult(BaseModel):
    has_text: bool = Field(description="图片中是否包含可识别的文字")
    recognized_text: str = Field(
        description="识别出的所有文字内容。如果无文字，则为空字符串"
    )


class AIService:
    """
    使用 zhenxun.llm 服务的 AI 图片识别封装。
    """

    _enabled: bool | None = None
    _model_name: str | None = None

    @classmethod
    def _initialize_config(cls):
        """延迟加载配置，避免循环导入问题。"""
        if cls._enabled is None:
            quote_config = Config.get("quote")
            cls._enabled = quote_config.get("AI_ENABLED", False)
            cls._model_name = quote_config.get("OCR_AI_MODEL")

    @classmethod
    async def recognize_image(cls, image_path: str | Path) -> str | None:
        """
        使用配置的视觉模型识别图片中的文字。

        返回:
            str | None: 识别出的文本内容，如果无文本或失败则返回 None。
        """
        cls._initialize_config()

        if not cls._enabled:
            logger.debug("AI 功能未启用，跳过 AI 识别", "群聊语录-AI")
            return None


        prompt = (
            "你是一个顶级的图像文字识别（OCR）引擎。"
            "请仔细分析这张图片，提取其中所有的文字内容。"
        )

        try:
            logger.info(
                f"使用模型 '{cls._model_name}' 进行AI-OCR识别: {image_path}",
                "群聊语录-AI",
            )

            message_to_analyze = UniMessage(
                [Text(prompt), Image(path=Path(image_path))]
            )

            ocr_result = await generate_structured(
                message=message_to_analyze,
                model=cls._model_name,
                instruction="你是一位专业的AI分析助手。请深入、全面地分析用户提供的所有内容（包括文本、图片、文件等），并给出结论。",
                response_model=OCRResult,
            )

            if ocr_result.has_text and ocr_result.recognized_text:
                return ocr_result.recognized_text
            else:
                return ""

        except LLMException as e:
            logger.error(
                f"AI-OCR 调用失败: {e.user_friendly_message}", "群聊语录-AI", e=e
            )
        except Exception as e:
            logger.error(f"AI-OCR 过程中发生未知错误: {e}", "群聊语录-AI", e=e)

        return None
