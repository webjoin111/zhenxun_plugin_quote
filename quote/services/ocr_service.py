import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

from zhenxun.configs.config import Config
from zhenxun.services.log import logger

from .ai_service import AIService


class OCRService:
    """OCR服务类"""

    _instance = None

    _engine_instance = None
    _engine_name: str | None = None
    _use_gpu: bool = False

    _thread_executor = ThreadPoolExecutor(max_workers=2)

    _cache: ClassVar[dict[str, str]] = {}

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

            cls._initialized = True
        except Exception as e:
            logger.error(f"OCR服务初始化失败: {e}", "群聊语录", e=e)
            cls._initialized = False
            raise e

    @classmethod
    async def _get_easyocr_instance(cls):
        """获取EasyOCR实例（延迟加载）"""
        loop = asyncio.get_running_loop()

        def _load_easyocr():
            try:
                import easyocr

                return easyocr.Reader(["ch_sim", "en"], gpu=cls._use_gpu)
            except Exception as e:
                logger.error(f"加载EasyOCR失败: {e}", "群聊语录", e=e)
                return None

        return await loop.run_in_executor(cls._thread_executor, _load_easyocr)

    @classmethod
    async def _get_paddleocr_instance(cls):
        """获取PaddleOCR实例（延迟加载）"""
        loop = asyncio.get_running_loop()

        def _load_paddleocr():
            try:
                from paddleocr import PaddleOCR

                return PaddleOCR(
                    use_angle_cls=True, lang="ch", use_gpu=cls._use_gpu, show_log=False
                )
            except Exception as e:
                logger.error(f"加载PaddleOCR失败: {e}", "群聊语录", e=e)
                return None

        return await loop.run_in_executor(cls._thread_executor, _load_paddleocr)

    @classmethod
    async def _run_easyocr(cls, image_path: str) -> str:
        """运行EasyOCR识别"""
        if not cls._engine_instance:
            cls._engine_instance = await cls._get_easyocr_instance()
            if not cls._engine_instance:
                return ""

        loop = asyncio.get_running_loop()

        def _recognize():
            try:
                if cls._engine_instance is None:
                    return ""
                result = cls._engine_instance.readtext(image_path)
                text = " ".join([item[1] for item in result]) if result else ""
                return text
            except Exception as e:
                logger.error(f"EasyOCR识别失败: {e}", "群聊语录", e=e)
                return ""

        return await loop.run_in_executor(cls._thread_executor, _recognize)

    @classmethod
    async def _run_paddleocr(cls, image_path: str) -> str:
        """运行PaddleOCR识别"""
        if not cls._engine_instance:
            cls._engine_instance = await cls._get_paddleocr_instance()
            if not cls._engine_instance:
                return ""

        loop = asyncio.get_running_loop()

        def _recognize():
            try:
                if cls._engine_instance is None:
                    return ""
                result = cls._engine_instance.ocr(image_path)
                if result and result[0]:
                    text = " ".join([item[1][0] for item in result[0]])
                    return text
                return ""
            except Exception as e:
                logger.error(f"PaddleOCR识别失败: {e}", "群聊语录", e=e)
                return ""

        return await loop.run_in_executor(cls._thread_executor, _recognize)

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

                if cls._engine_name == "easyocr":
                    ocr_content = await cls._run_easyocr(image_path)
                elif cls._engine_name == "paddleocr":
                    ocr_content = await cls._run_paddleocr(image_path)
                else:
                    logger.warning(
                        f"未知的OCR引擎: {cls._engine_name}，使用EasyOCR", "群聊语录"
                    )
                    ocr_content = await cls._run_easyocr(image_path)

                if not ocr_content:
                    fallback_engine = (
                        "easyocr" if cls._engine_name == "paddleocr" else "paddleocr"
                    )
                    logger.debug(
                        f"主引擎识别失败，尝试使用{fallback_engine}作为备选", "群聊语录"
                    )

                    cls._engine_instance = None
                    original_engine = cls._engine_name
                    cls._engine_name = fallback_engine

                    if fallback_engine == "easyocr":
                        ocr_content = await cls._run_easyocr(image_path)
                    else:
                        ocr_content = await cls._run_paddleocr(image_path)

                    cls._engine_name = original_engine
                    cls._engine_instance = None

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
        cls._engine_instance = None
        cls._initialized = False
        logger.debug("OCR服务已关闭", "群聊语录")
