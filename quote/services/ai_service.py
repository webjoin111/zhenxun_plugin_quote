from pathlib import Path
import json

from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.services.llm import analyze_multimodal, LLMException, get_model_instance
from zhenxun.services.llm.types import LLMResponse


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
        try:
            model_instance = await get_model_instance(cls._model_name)
            if not model_instance.can_process_images():
                logger.error(
                    f"配置的模型 '{cls._model_name}' 不支持图像处理功能。 "
                    f"请在配置中更换为支持视觉的模型（如 Gemini-Flash, GLM-4V 等）。",
                    "群聊语录-AI",
                )
                return None
        except LLMException as e:
            logger.error(
                f"获取模型实例 '{cls._model_name}' 失败: {e}", "群聊语录-AI", e=e
            )
            return None
        finally:
            if "model_instance" in locals() and model_instance:
                await model_instance.close()

        prompt = (
            "你是一个顶级的图像文字识别（OCR）引擎。"
            "请仔细分析这张图片，提取其中所有的文字内容。"
            "请将结果以一个 JSON 对象的形式返回，该对象应包含以下键："
            "1. `has_text` (布尔值): 图片中是否包含可识别的文字。"
            "2. `recognized_text` (字符串): 识别出的所有文字内容。如果无文字，则此字段为空字符串。"
            "请不要返回除此 JSON 对象之外的任何额外解释、注释或标记。"
        )

        try:
            logger.info(
                f"使用模型 '{cls._model_name}' 进行AI-OCR识别: {image_path}",
                "群聊语录-AI",
            )
            response = await analyze_multimodal(
                text=prompt, images=[Path(image_path)], model=cls._model_name
            )

            if isinstance(response, LLMResponse):
                response_text = response.text.strip()
            elif isinstance(response, str):
                response_text = response.strip()
            else:
                logger.warning(
                    f"AI-OCR 返回了未知类型: {type(response)}", "群聊语录-AI"
                )
                return None

            if response_text.startswith("```json"):
                response_text = response_text[7:-3].strip()

            data = json.loads(response_text)

            if data.get("has_text") and data.get("recognized_text"):
                return data["recognized_text"]
            else:
                return ""

        except json.JSONDecodeError:
            logger.error(
                f"AI-OCR 返回的不是有效的 JSON: {response_text}", "群聊语录-AI"
            )
        except LLMException as e:
            logger.error(
                f"AI-OCR 调用失败: {e.user_friendly_message}", "群聊语录-AI", e=e
            )
        except Exception as e:
            logger.error(f"AI-OCR 过程中发生未知错误: {e}", "群聊语录-AI", e=e)

        return None
