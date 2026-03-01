import asyncio
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any, ClassVar

from cachetools import TTLCache
from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .ai_service import AIService


class OCREngine(ABC):
    """OCR引擎抽象基类"""

    def __init__(self, use_gpu: bool):
        self.use_gpu = use_gpu
        self._model = None

    @abstractmethod
    def load_model(self) -> Any:
        """加载模型"""
        pass

    @abstractmethod
    def recognize(self, image_path: str) -> str:
        """识别图片文本"""
        pass

    def ensure_model(self):
        """确保模型已加载"""
        if self._model is None:
            self._model = self.load_model()
        return self._model


class EasyOCREngine(OCREngine):
    """EasyOCR 引擎实现"""

    def load_model(self):
        try:
            import easyocr

            return easyocr.Reader(["ch_sim", "en"], gpu=self.use_gpu)
        except Exception as e:
            logger.error(f"加载EasyOCR失败: {e}", "群聊语录", e=e)
            return None

    def recognize(self, image_path: str) -> str:
        model = self.ensure_model()
        if not model:
            return ""
        try:
            result = model.readtext(image_path)
            return " ".join([item[1] for item in result]) if result else ""
        except Exception as e:
            logger.error(f"EasyOCR识别失败: {e}", "群聊语录", e=e)
            return ""


class PaddleOCREngine(OCREngine):
    """PaddleOCR 引擎实现"""

    def load_model(self):
        try:
            from paddleocr import PaddleOCR

            return PaddleOCR(
                use_angle_cls=True, lang="ch", use_gpu=self.use_gpu, show_log=False
            )
        except Exception as e:
            logger.error(f"加载PaddleOCR失败: {e}", "群聊语录", e=e)
            return None

    def recognize(self, image_path: str) -> str:
        model = self.ensure_model()
        if not model:
            return ""
        try:
            result = model.ocr(image_path)
            if result and result[0]:
                return " ".join([item[1][0] for item in result[0]])
            return ""
        except Exception as e:
            logger.error(f"PaddleOCR识别失败: {e}", "群聊语录", e=e)
            return ""


class OCRService:
    """OCR服务类"""

    _instance = None

    _strategy: OCREngine | None = None
    _engine_name: str | None = None
    _use_gpu: bool = False

    _thread_executor = ThreadPoolExecutor(max_workers=2)

    _cache: ClassVar[TTLCache] = TTLCache(maxsize=1000, ttl=3600)

    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    async def initialize_engine(cls) -> None:
        """初始化OCR引擎"""
        if cls._initialized:
            return

        try:
            quote_config = Config.get("quote")
            if quote_config:
                cls._engine_name = quote_config.get("OCR_ENGINE", "easyocr")
                cls._use_gpu = quote_config.get("OCR_USE_GPU", False)
            else:
                cls._engine_name = "easyocr"
                cls._use_gpu = False

            if cls._engine_name not in ["easyocr", "paddleocr"]:
                logger.warning(
                    f"无效的OCR引擎: {cls._engine_name}，使用默认引擎 easyocr",
                    "群聊语录",
                )
                cls._engine_name = "easyocr"

            logger.info(
                f"OCR服务初始化 - 引擎: {cls._engine_name}, GPU: {cls._use_gpu}",
                "群聊语录",
            )

            if cls._engine_name == "paddleocr":
                cls._strategy = PaddleOCREngine(cls._use_gpu)
            else:
                cls._strategy = EasyOCREngine(cls._use_gpu)

            cls._initialized = True
        except Exception as e:
            logger.error(f"OCR服务初始化失败: {e}", "群聊语录", e=e)
            cls._initialized = False
            raise e

    @classmethod
    async def _execute_strategy(cls, strategy: OCREngine, image_path: str) -> str:
        """在线程池中执行OCR策略"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            cls._thread_executor, strategy.recognize, image_path
        )

    @classmethod
    async def recognize_text(cls, image_path: str) -> str:
        """识别图片中的文字"""
        if not cls._initialized:
            await cls.initialize_engine()

        if image_path in cls._cache:
            logger.debug(f"使用OCR缓存: {image_path}", "群聊语录")
            return cls._cache[image_path]

        try:
            ai_content = await AIService.recognize_image(image_path)

            if ai_content is not None:
                logger.debug(f"AI识别成功，文本长度: {len(ai_content)}", "群聊语录")
                cls._cache[image_path] = ai_content
                return ai_content
            else:
                logger.debug("AI识别失败或未启用，降级使用本地OCR引擎", "群聊语录")

                if not cls._strategy:
                    cls._strategy = EasyOCREngine(cls._use_gpu)

                ocr_content = await cls._execute_strategy(cls._strategy, image_path)

                if not ocr_content:
                    fallback_engine = (
                        "easyocr"
                        if isinstance(cls._strategy, PaddleOCREngine)
                        else "paddleocr"
                    )
                    logger.debug(
                        f"主引擎识别失败，尝试使用{fallback_engine}作为备选", "群聊语录"
                    )

                    fallback_strategy = (
                        EasyOCREngine(cls._use_gpu)
                        if fallback_engine == "easyocr"
                        else PaddleOCREngine(cls._use_gpu)
                    )
                    ocr_content = await cls._execute_strategy(
                        fallback_strategy, image_path
                    )

                if ocr_content:
                    cls._cache[image_path] = ocr_content
                    logger.debug(
                        f"本地OCR识别成功，文本长度: {len(ocr_content)}", "群聊语录"
                    )
                else:
                    logger.warning(
                        f"所有OCR引擎均识别失败，未能提取文本: {image_path}", "群聊语录"
                    )
                return ocr_content
        except Exception as e:
            logger.error(f"文本识别过程发生异常: {e}", "群聊语录", e=e)
            return ""

    @classmethod
    def clear_cache(cls) -> None:
        """清除OCR结果缓存"""
        cls._cache.clear()
        logger.debug("OCR结果缓存已清除", "群聊语录")

    @classmethod
    def get_cache_size(cls) -> int:
        """获取缓存大小

        返回:
            int: 缓存中的项目数
        """
        return len(cls._cache)

    @classmethod
    def shutdown(cls) -> None:
        """关闭OCR服务，释放资源"""
        cls._thread_executor.shutdown(wait=False)
        cls._strategy = None
        cls._initialized = False
        logger.debug("OCR服务已关闭", "群聊语录")
