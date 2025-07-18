from tortoise import fields
from zhenxun.services.db_context import Model

QUOTE_CACHE_TYPE = "QUOTE_CACHE"


class Quote(Model):
    """语录模型"""

    id = fields.IntField(pk=True, generated=True)
    """主键ID"""

    group_id = fields.CharField(max_length=64, index=True)
    """群组ID"""

    image_path = fields.CharField(max_length=255, unique=True)
    """图片路径"""

    image_hash = fields.CharField(max_length=64, null=True, index=True)
    """图片哈希值，用于检测重复图片"""

    ocr_text = fields.TextField(null=True)
    """OCR识别文本"""

    recorded_text = fields.TextField(null=True)
    """记录的文本"""

    tags = fields.JSONField(default=list)
    """标签列表"""

    quoted_user_id = fields.CharField(max_length=64, null=True, index=True)
    """被记录用户的QQ号"""

    uploader_user_id = fields.CharField(max_length=64, null=True, index=True)
    """上传者的QQ号"""

    created_at = fields.DatetimeField(auto_now_add=True)
    """创建时间"""

    view_count = fields.IntField(default=0)
    """查看次数"""

    cache_type = QUOTE_CACHE_TYPE
    """缓存类型"""
    cache_key_field = "id"
    """缓存键字段 (使用语录的ID作为缓存的唯一键)"""

    class Meta:
        table = "quote"
        table_description = "语录表"
