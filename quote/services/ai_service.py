import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from typing import ClassVar

import httpx

from zhenxun.configs.config import Config
from zhenxun.services.log import logger


class AIService:
    """Gemini API图像识别服务"""

    _instance = None
    _api_key: str = ""
    _model_name: str = "gemini-2.0-flash-exp"
    _api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    _enabled: bool = False
    _thread_executor = ThreadPoolExecutor(max_workers=2)
    _cache: ClassVar[dict[str, str]] = {}
    _initialized = False
    _consecutive_failures: int = 0
    _max_failures: int = 3
    _max_retries: int = 3

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def initialize(cls) -> None:
        """初始化服务"""
        if cls._initialized:
            return

        try:
            quote_config = Config.get("quote")
            if quote_config:
                ai_config = quote_config.get("AI_CONFIG", {})
                if isinstance(ai_config, dict):
                    cls._api_key = ai_config.get("api_key", "")
                    cls._model_name = ai_config.get(
                        "model_name", "gemini-2.0-flash-exp"
                    )
                    cls._api_base = ai_config.get(
                        "api_base", "https://generativelanguage.googleapis.com/v1beta"
                    )
                else:
                    logger.warning("AI_CONFIG不是字典格式，使用默认配置", "群聊语录")

                cls._enabled = quote_config.get("AI_ENABLED", False)

            if cls._enabled and not cls._api_key:
                logger.warning("AI功能已启用但API密钥为空，将禁用AI功能", "群聊语录")
                cls._enabled = False

            masked_key = (
                cls._api_key[:10] + "******" + cls._api_key[-6:]
                if len(cls._api_key) > 16
                else "******"
            )

            logger.debug(
                f"AI服务初始化 - 启用: {cls._enabled}, 模型: {cls._model_name}, "
                f"API密钥: {masked_key}",
                "群聊语录",
            )

            cls._consecutive_failures = 0
            cls._initialized = True
        except Exception as e:
            logger.error(f"AI服务初始化失败: {e}", "群聊语录", e=e)
            cls._initialized = False
            cls._enabled = False
            raise e

    @classmethod
    async def reset_service(cls) -> None:
        """重置服务"""
        logger.warning(
            f"AI服务连续失败{cls._consecutive_failures}次，执行重置操作", "群聊语录"
        )

        cls._initialized = False

        cls._cache.clear()

        await cls.initialize()

        logger.info("AI服务已重置", "群聊语录")

    @classmethod
    async def recognize_image(cls, image_path: str) -> str | None:
        """识别图片文字"""
        if not cls._initialized:
            await cls.initialize()

        if not cls._enabled or not cls._api_key:
            logger.info("AI功能未启用或API密钥为空，跳过AI识别", "群聊语录")
            return None

        if image_path in cls._cache:
            logger.debug(f"使用AI识别缓存: {image_path}", "群聊语录")
            cls._consecutive_failures = 0
            return cls._cache[image_path]

        for retry_count in range(cls._max_retries):
            try:
                if retry_count > 0:
                    logger.debug(
                        f"AI识别重试 ({retry_count}/{cls._max_retries - 1})", "群聊语录"
                    )

                    await asyncio.sleep(1.0)

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    cls._thread_executor, lambda: cls._recognize_image_sync(image_path)
                )

                if result is not None:
                    cls._consecutive_failures = 0
                    cls._cache[image_path] = result

                    if retry_count > 0:
                        logger.debug(
                            f"AI识别重试成功 (第{retry_count + 1}次尝试)，"
                            f"文本长度: {len(result)}",
                            "群聊语录",
                        )
                    else:
                        logger.debug(f"AI识别成功，文本长度: {len(result)}", "群聊语录")

                    return result

                logger.debug(
                    f"AI识别尝试失败 ({retry_count + 1}/{cls._max_retries})", "群聊语录"
                )

            except Exception as e:
                logger.error(
                    f"AI识别尝试异常 ({retry_count + 1}/{cls._max_retries}): {e}",
                    "群聊语录",
                    e=e,
                )

        cls._consecutive_failures += 1
        logger.debug(
            f"AI识别所有重试均失败，连续失败次数: "
            f"{cls._consecutive_failures}/{cls._max_failures}",
            "群聊语录",
        )

        if cls._consecutive_failures >= cls._max_failures:
            await cls.reset_service()

        return None

    @classmethod
    def _recognize_image_sync(cls, image_path: str) -> str | None:
        """同步识别图片文字"""
        import base64
        import mimetypes

        try:
            with open(image_path, "rb") as image_file:
                image_data = image_file.read()

            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                mime_type = "image/png"

            base64_image = base64.b64encode(image_data).decode("utf-8")

            url = (
                f"{cls._api_base}/models/{cls._model_name}:generateContent"
                f"?key={cls._api_key}"
            )

            prompt = (
                "请识别这张图片中的所有文字，只返回文字内容，不要添加任何解释或描述。"
                "如果图片中没有文字，请回复'无文字内容'。"
            )

            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": base64_image,
                                }
                            },
                        ]
                    }
                ],
                "generation_config": {
                    "temperature": 0.1,
                    "top_p": 0.95,
                    "top_k": 40,
                    "max_output_tokens": 2048,
                },
            }

            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )

            response_json = response.json()
            logger.debug(
                f"Gemini API响应: {json.dumps(response_json, ensure_ascii=False)}",
                "群聊语录",
            )

            if response.status_code != 200:
                logger.error(
                    f"Gemini API请求失败: 状态码 {response.status_code}, "
                    f"响应: {response.text}",
                    "群聊语录",
                )
                return None

            candidates = response_json.get("candidates", [])
            if candidates:
                candidate = candidates[0]
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                if parts and "text" in parts[0]:
                    text = parts[0]["text"].strip()
                    if text == "无文字内容":
                        return ""
                    return text

            logger.debug("无法从Gemini API响应中提取文本", "群聊语录")
            return None
        except Exception as e:
            logger.error(f"Gemini API调用失败: {e}", "群聊语录", e=e)
            return None

    @classmethod
    def clear_cache(cls) -> None:
        """清除缓存"""
        cls._cache.clear()
        logger.debug("AI识别结果缓存已清除", "群聊语录")

    @classmethod
    def get_cache_size(cls) -> int:
        """获取缓存数量"""
        return len(cls._cache)

    @classmethod
    def shutdown(cls) -> None:
        """关闭服务"""
        cls._thread_executor.shutdown(wait=False)
        cls._initialized = False
        logger.debug("AI服务已关闭", "群聊语录")
