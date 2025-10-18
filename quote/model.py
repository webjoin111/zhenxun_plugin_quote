from collections.abc import Iterable
from typing import Any
from tortoise import fields
from pydantic import BaseModel, Field
from zhenxun.services.db_context import Model
from zhenxun.ui.models import RenderableComponent
from zhenxun.ui.models.core.base import ContainerComponent

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


class QuotedReplyData(BaseModel):
    """被引用消息的数据模型"""

    avatar_data_url: str
    """被引用者的头像Base64数据URI"""
    author: str
    """被引用者的名称"""
    text: Any
    """被引用消息的内容(可以是字符串或内容列表)"""


class QuoteCardData(RenderableComponent):
    """语录卡片的数据模型"""

    avatar_data_url: str
    """头像的Base64数据URI"""
    text: Any
    """语录文本或内容列表"""
    author: str
    """作者名"""
    author_role: str | None = Field(default=None, description="作者角色 (owner, admin)")
    author_level: str | None = Field(default=None, description="作者等级 (如 'LV81')")
    author_title: str | None = Field(default=None, description="作者群头衔")
    quoted_reply: QuotedReplyData | None = Field(
        default=None, description="被引用的消息内容"
    )

    @property
    def template_name(self) -> str:
        # 根据 variant 动态选择模板路径
        if self.variant and self.variant.startswith("classic"):
            return "@quote/components/classic"
        return "@quote/components/qq-native"

    variant: str | None = Field(default=None, description="要使用的皮肤/主题名称")


class QuoteSequenceData(ContainerComponent):
    """多条语录序列的数据模型"""

    messages: list[QuoteCardData] = Field(description="连续的消息卡片列表")
    """连续的消息卡片列表"""
    variant: str | None = Field(default=None, description="要使用的皮肤/主题名称")
    """要使用的皮肤/主题名称"""

    def get_children(self) -> Iterable[RenderableComponent]:
        """将消息卡片列表作为子组件暴露给渲染器"""
        yield from self.messages

    @property
    def template_name(self) -> str:
        return "@quote/pages/quote-sequence-page"


class HotQuoteItemData(BaseModel):
    """热门语录排行榜中的单个项目"""

    rank: int
    quote_id: int
    user_name: str
    avatar_data_url: str
    preview_text: str
    is_image_quote: bool
    image_path: str
    view_count: int


class HotQuotesPageData(RenderableComponent):
    """热门语录页面的数据模型"""

    group_id: str
    quotes: list[HotQuoteItemData]

    @property
    def template_name(self) -> str:
        return "@quote/pages/hot_quotes"
