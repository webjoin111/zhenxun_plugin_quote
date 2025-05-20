class NetworkError(Exception):
    """网络错误异常"""
    pass


class ImageProcessError(Exception):
    """图片处理错误异常"""
    pass


class OCRError(Exception):
    """OCR处理错误异常"""
    pass


class ReplyImageNotFoundException(Exception):
    """回复消息中未找到图片错误"""
    pass
