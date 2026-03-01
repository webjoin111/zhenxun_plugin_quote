import os
from pathlib import Path
import random
import base64
from typing import Any, ClassVar

from cachetools import TTLCache
from nonebot.adapters.onebot.v11 import Bot
from tortoise.expressions import Q
from tortoise.functions import Count

from zhenxun import ui
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services import avatar_service, logger
from zhenxun.utils.echart_utils import ChartUtils
from zhenxun.utils.echart_utils.models import Barh
from zhenxun.utils.platform import PlatformUtils
import aiofiles

from ..config import DATA_PATH, resolve_quote_image_path
from ..model import HotQuoteItemData, HotQuotesPageData, Quote, QuoteCardData

try:
    import spacy_pkuseg as pkuseg

    seg = pkuseg.pkuseg(model_name="web")
except ImportError:
    logger.warning(
        "未安装 'spacy_pkuseg'，分词功能将受限。请运行 `pip install zhenxun[pkuseg]`",
        "群聊语录",
    )

    class DummySeg:
        def cut(self, text):
            return [text] if text else []

    seg = DummySeg()


class QuoteService:
    """语录服务类"""

    _recent_quotes: ClassVar[TTLCache] = TTLCache(maxsize=1000, ttl=600)
    _max_history_per_key: ClassVar[int] = 10

    @staticmethod
    async def add_quote(
        group_id: str,
        image_path: str,
        ocr_content: str | None,
        recorded_text: str | None,
        quoted_user_id: str | None = None,
        image_hash: str | None = None,
        uploader_user_id: str | None = None,
    ) -> tuple[Quote | None, bool]:
        """
        向数据库添加语录，并在内部处理所有重复性检查。
        """
        try:
            logger.info(
                f"开始添加语录 - 群组: {group_id}, 图片路径: {image_path}, 被记录用户: {quoted_user_id}, 上传者: {uploader_user_id}",
                "群聊语录",
            )
            if image_hash:
                existing_quote = await Quote.filter(
                    group_id=group_id, image_hash=image_hash
                ).first()
                if existing_quote:
                    logger.warning(
                        f"发现重复语录 (基于图片哈希值) - 群组: {group_id}, 已存在ID: {existing_quote.id}",
                        "群聊语录",
                    )
                    return existing_quote, False

            tags_source = ocr_content if ocr_content else recorded_text
            tags = QuoteService.cut_sentence(tags_source) if tags_source else []

            relative_image_path = os.path.relpath(image_path, DATA_PATH)
            relative_image_path = Path(relative_image_path).as_posix()

            quote = await Quote.create(
                group_id=group_id,
                image_path=relative_image_path,
                image_hash=image_hash,
                ocr_text=ocr_content,
                recorded_text=recorded_text,
                tags=tags,
                quoted_user_id=quoted_user_id,
                uploader_user_id=uploader_user_id,
                view_count=0,
            )

            logger.info(f"语录添加成功 - ID: {quote.id}, 群组: {group_id}", "群聊语录")
            return quote, True

        except Exception as e:
            logger.error(f"添加语录失败 - 群组: {group_id}, 错误: {e}", "群聊语录", e=e)
            return None, False

    @staticmethod
    async def delete_quote(group_id: str, image_basename: str) -> bool:
        """从数据库删除语录并删除对应的图片文件"""
        try:
            logger.info(
                f"尝试删除语录 - 群组: {group_id}, 图片: {image_basename}", "群聊语录"
            )
            quote = await QuoteService.find_quote_by_basename(group_id, image_basename)
            if quote:
                absolute_image_path = resolve_quote_image_path(quote.image_path)
                if os.path.exists(absolute_image_path):
                    try:
                        os.remove(absolute_image_path)
                        logger.info(
                            f"图片文件删除成功: {absolute_image_path}", "群聊语录"
                        )
                    except Exception as file_error:
                        logger.warning(
                            f"删除图片文件失败: {absolute_image_path}, 错误: {file_error}",
                            "群聊语录",
                            e=file_error,
                        )
                else:
                    logger.warning(f"图片文件不存在: {absolute_image_path}", "群聊语录")

                await Quote.filter(id=quote.id).delete()
                logger.info(
                    f"语录删除成功 - ID: {quote.id}, 群组: {group_id}", "群聊语录"
                )
                return True
            else:
                logger.warning(
                    f"要删除的语录不存在 - 群组: {group_id}, 图片: {image_basename}",
                    "群聊语录",
                )
                return False
        except Exception as e:
            logger.error(f"删除语录失败 - 群组: {group_id}, 错误: {e}", "群聊语录", e=e)
            return False

    @classmethod
    async def get_random_quote(
        cls, group_id: str, user_id_filter: str | None = None
    ) -> Quote | None:
        """随机获取一条语录，可根据用户筛选，并避免短时间内重复展示"""
        try:
            logger.info(
                f"尝试随机获取语录 - 群组: {group_id}, 用户筛选: {user_id_filter}",
                "群聊语录",
            )
            query_filters = {"group_id": group_id}
            if user_id_filter:
                query_filters["quoted_user_id"] = user_id_filter

            count = await Quote.filter(**query_filters).count()
            if count == 0:
                logger.info(
                    f"群组 {group_id} 中 (用户: {user_id_filter or '任意'}) 没有语录",
                    "群聊语录",
                )
                return None

            memory_key = f"{group_id}_{user_id_filter or 'all'}"

            quotes = await Quote.filter(**query_filters)

            if not quotes:
                return None

            recent_ids = cls._recent_quotes.get(memory_key) or []
            if recent_ids and count > cls._max_history_per_key:
                unseen_quotes = [q for q in quotes if q.id not in recent_ids]
                if unseen_quotes:
                    quotes = unseen_quotes
                else:
                    logger.warning(
                        f"所有语录 ({memory_key}) 都已展示过，将重置记忆",
                        "群聊语录",
                    )
                    cls._recent_quotes[memory_key] = []

            quote = cls._select_and_record_quote(memory_key, quotes)

            if quote:
                logger.info(
                    f"随机获取到语录 ID: {quote.id} (路径: {quote.image_path}) 来自群组 {group_id} (用户: {user_id_filter or '任意'})",
                    "群聊语录",
                )
                return quote
            else:
                logger.warning(
                    f"随机获取语录失败，即使 count > 0 (count={count}) - {memory_key}",
                    "群聊语录",
                )
                return None
        except Exception as e:
            logger.error(
                f"随机获取语录时发生错误 - 群组: {group_id}, 用户筛选: {user_id_filter}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @classmethod
    async def _search_quotes_by_text_and_filter_by_tags(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> list[Quote]:
        """
        分阶段搜索语录：
        1. [精确匹配] 首先尝试匹配完整的关键词。
        2. [模糊匹配] 如果没有精确匹配结果，则回退到分词模糊搜索。
        """
        base_filters = {"group_id": group_id}
        if user_id_filter:
            base_filters["quoted_user_id"] = user_id_filter

        logger.info(
            f"第一阶段：尝试对 '{keyword}' 进行精确匹配搜索...", "群聊语录-搜索"
        )
        exact_match_query = Q(ocr_text__icontains=keyword) | Q(
            recorded_text__icontains=keyword
        )
        exact_matches = await Quote.filter(exact_match_query, **base_filters)

        if exact_matches:
            logger.info(
                f"精确匹配成功，找到 {len(exact_matches)} 条语录。", "群聊语录-搜索"
            )
            return exact_matches

        logger.info("精确匹配未找到结果，回退到分词模糊搜索...", "群聊语录-搜索")
        keywords = [k.strip() for k in keyword.split() if k.strip()]
        if not keywords:
            return []

        text_query_condition = Q()
        for kw in keywords:
            kw_tokens = cls.cut_sentence(kw)
            single_kw_text_condition = Q(ocr_text__icontains=kw) | Q(
                recorded_text__icontains=kw
            )
            for token in kw_tokens:
                single_kw_text_condition |= Q(ocr_text__icontains=token)
                single_kw_text_condition |= Q(recorded_text__icontains=token)

            text_query_condition &= single_kw_text_condition

        candidate_quotes = await Quote.filter(text_query_condition, **base_filters)
        logger.debug(
            f"数据库模糊搜索初步匹配到 {len(candidate_quotes)} 条语录", "群聊语录"
        )
        if not candidate_quotes:
            return []

        final_matches = []
        for quote in candidate_quotes:
            if all(cls._check_single_keyword_in_quote(kw, quote) for kw in keywords):
                final_matches.append(quote)

        logger.debug(f"经过最终过滤后，匹配到 {len(final_matches)} 条语录", "群聊语录")
        return final_matches

    @classmethod
    def _check_single_keyword_in_quote(cls, keyword: str, quote: Quote) -> bool:
        """
        检查单个关键词（及其分词）是否存在于语录的文本或标签中。
        这是一个在Python层面执行的辅助函数。
        """
        kw_lower = keyword.lower()
        tokens = cls.cut_sentence(keyword)

        if quote.ocr_text and kw_lower in quote.ocr_text.lower():
            return True
        if quote.recorded_text and kw_lower in quote.recorded_text.lower():
            return True
        for token in tokens:
            if quote.ocr_text and token.lower() in quote.ocr_text.lower():
                return True
            if quote.recorded_text and token.lower() in quote.recorded_text.lower():
                return True

        tags_to_check = quote.tags if isinstance(quote.tags, list) else []
        for tag in tags_to_check:
            tag_lower = str(tag).lower()
            if kw_lower in tag_lower:
                return True
            for token in tokens:
                if token.lower() in tag_lower:
                    return True

        return False

    @classmethod
    async def search_quote(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> Quote | None:
        """根据关键词搜索语录，可根据用户筛选。已重构为使用两阶段查询方法。"""
        logger.info(
            f"开始搜索语录 - 群组: {group_id}, 关键词: {keyword}, 用户筛选: {user_id_filter}",
            "群聊语录",
        )
        memory_key = f"{group_id}_{user_id_filter or 'all'}_{keyword}"

        try:
            all_matches = await cls._search_quotes_by_text_and_filter_by_tags(
                group_id, keyword, user_id_filter
            )

            if all_matches:
                logger.info(f"总共找到匹配的语录 {len(all_matches)} 条", "群聊语录")
                random_quote = cls._select_and_record_quote(memory_key, all_matches)
                logger.info(
                    f"搜索到语录 ID: {random_quote.id} (路径: {random_quote.image_path})",
                    "群聊语录",
                )
                return random_quote

            logger.info(
                f"群组 {group_id} (用户: {user_id_filter or '任意'}) 中未找到与 '{keyword}' 相关的语录。",
                "群聊语录",
            )
            return None
        except Exception as e:
            logger.error(
                f"搜索语录时发生错误 - 群组: {group_id}, 关键词: {keyword}, "
                f"用户筛选: {user_id_filter}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    async def find_quote_by_basename(
        group_id: str, image_basename: str
    ) -> Quote | None:
        """根据图片文件名查找语录"""
        try:
            logger.info(
                f"根据文件名查找语录 - 群组: {group_id}, 文件名: {image_basename}",
                "群聊语录",
            )

            quotes = await Quote.filter(
                group_id=group_id, image_path__iendswith=image_basename
            )
            quote = quotes[0] if quotes else None

            if quote:
                logger.info(
                    f"找到语录 ID: {quote.id} (路径: {quote.image_path})",
                    "群聊语录",
                )
                return quote

            logger.info(f"未找到包含文件名 {image_basename} 的语录", "群聊语录")
            return None
        except Exception as e:
            logger.error(
                f"根据文件名查找语录时发生错误 - 群组: {group_id}, 文件名: {image_basename}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    async def get_last_quote(group_id: str) -> Quote | None:
        """获取群组内最后保存的一条语录"""
        try:
            return await Quote.filter(group_id=group_id).order_by("-id").first()
        except Exception:
            return None

    @staticmethod
    async def get_all_quotes() -> list[Quote]:
        """获取所有语录"""
        try:
            logger.info("开始获取所有语录", "群聊语录")
            quotes = await Quote.all()
            logger.info(f"获取所有语录成功 - 总数: {len(quotes)}", "群聊语录")
            return quotes
        except Exception as e:
            logger.error(f"获取所有语录失败 - 错误: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def add_tags(quote: Quote, tags: list[str]) -> bool:
        """为语录添加标签"""
        try:
            logger.info(f"为语录 ID: {quote.id} 添加标签: {tags}", "群聊语录")
            current_tags = set(quote.tags)
            new_tags = set(tags)

            updated_tags = list(current_tags.union(new_tags))
            quote.tags = updated_tags
            await quote.save()

            logger.info(
                f"语录 ID: {quote.id} 标签更新成功，现有标签: {updated_tags}",
                "群聊语录",
            )
            return True
        except Exception as e:
            logger.error(
                f"为语录添加标签失败 - 语录 ID: {quote.id}, 错误: {e}", "群聊语录", e=e
            )
            return False

    @staticmethod
    async def delete_tags(quote: Quote, tags: list[str]) -> bool:
        """删除语录的标签"""
        try:
            logger.info(f"从语录 ID: {quote.id} 删除标签: {tags}", "群聊语录")
            current_tags = set(quote.tags)
            remove_tags = set(tags)

            updated_tags = list(current_tags - remove_tags)
            quote.tags = updated_tags
            await quote.save()

            logger.info(
                f"语录 ID: {quote.id} 标签删除成功，现有标签: {updated_tags}",
                "群聊语录",
            )
            return True
        except Exception as e:
            logger.error(
                f"删除语录标签失败 - 语录 ID: {quote.id}, 错误: {e}", "群聊语录", e=e
            )
            return False

    @classmethod
    def _select_and_record_quote(cls, memory_key: str, quotes: list[Quote]) -> Quote:
        """选择并记录语录"""
        if not quotes:
            raise ValueError("语录列表为空")

        recent_ids = cls._recent_quotes.get(memory_key) or []
        unseen_quotes = [q for q in quotes if q.id not in recent_ids]

        if unseen_quotes:
            selected_quote = random.choice(unseen_quotes)
        else:
            selected_quote = random.choice(quotes)

        if memory_key not in cls._recent_quotes:
            cls._recent_quotes[memory_key] = []

        cls._recent_quotes[memory_key].append(selected_quote.id)

        if len(cls._recent_quotes[memory_key]) > cls._max_history_per_key:
            cls._recent_quotes[memory_key].pop(0)

        return selected_quote

    @classmethod
    async def search_quotes_for_deletion(
        cls, group_id: str, keywords: list[str] | None = None, **filters: Any
    ) -> list[Quote]:
        """根据关键词（OR逻辑）或其他条件搜索语录，用于批量删除。"""
        logger.info(
            f"开始搜索语录用于删除 - 群组: {group_id}, 关键词: {keywords}, 过滤器: {filters}",
            "群聊语录",
        )

        try:
            query = Q(group_id=group_id)

            if keywords:
                keyword_query = Q()
                for kw in keywords:
                    keyword_query |= Q(ocr_text__icontains=kw)
                    keyword_query |= Q(recorded_text__icontains=kw)
                query &= keyword_query

            if filters:
                for key, value in filters.items():
                    if value is not None:
                        query &= Q(**{key: value})

            final_matched_quotes = await Quote.filter(query)

            logger.info(
                f"找到 {len(final_matched_quotes)} 条与条件匹配的语录",
                "群聊语录",
            )
            return final_matched_quotes

        except Exception as e:
            logger.error(
                f"搜索语录用于删除时发生错误 - 群组: {group_id}, 关键词: {keywords}, "
                f"过滤器: {filters}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return []

    @staticmethod
    async def find_quotes_from_left_users(group_id: str, bot: Bot) -> list[Quote]:
        """查找指定群组中由已退群用户产生或记录的语录"""
        logger.info(f"开始查找群组 {group_id} 中已退群用户的语录", "群聊语录")
        try:
            uploaders = await Quote.filter(
                group_id=group_id, uploader_user_id__not_isnull=True
            ).values_list("uploader_user_id", flat=True)
            quoted = await Quote.filter(
                group_id=group_id, quoted_user_id__not_isnull=True
            ).values_list("quoted_user_id", flat=True)
            all_quote_users = set(uploaders) | set(quoted)

            current_members_info = await PlatformUtils.get_group_member_list(
                bot, group_id
            )
            current_member_ids = {
                str(member.user_id) for member in current_members_info
            }

            left_user_ids = all_quote_users - current_member_ids

            if not left_user_ids:
                logger.info(f"群组 {group_id} 中没有发现已退群用户的语录", "群聊语录")
                return []

            logger.info(
                f"在群组 {group_id} 中找到 {len(left_user_ids)} 个已退群用户留下的语录记录。"
            )

            left_user_quotes = await Quote.filter(
                Q(group_id=group_id)
                & (
                    Q(uploader_user_id__in=list(left_user_ids))
                    | Q(quoted_user_id__in=list(left_user_ids))
                )
            ).all()

            return left_user_quotes
        except Exception as e:
            logger.error(f"查找已退群用户语录时发生错误: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def generate_temp_quote(
        avatar_bytes: bytes,
        text: Any,
        author: str,
        variant: str | None = None,
        author_role: str | None = None,
        author_title: str | None = None,
        author_level: str | None = None,
        quoted_reply: Any = None,
    ) -> bytes:
        """生成临时语录图片"""
        try:
            logger.info(
                f"开始生成临时语录图片 - 作者: {author}, 皮肤(variant): {variant}",
                "群聊语录",
            )

            avatar_base64 = base64.b64encode(avatar_bytes).decode("utf-8")

            quote_card = QuoteCardData(
                avatar_data_url=f"data:image/png;base64,{avatar_base64}",
                text=text,
                author=author,
                author_role=author_role,
                author_title=author_title,
                author_level=author_level,
                quoted_reply=quoted_reply,
                variant=variant,
            )

            img_data = await ui.render(quote_card)

            return img_data
        except Exception as e:
            logger.error(f"生成临时语录图片失败: {e}", "群聊语录", e=e)
            raise e

    @staticmethod
    async def increment_view_count(quote_id: int) -> None:
        """增加语录的查看次数"""
        try:
            quote = await Quote.get_or_none(id=quote_id)
            if quote:
                quote.view_count += 1
                await quote.save(update_fields=["view_count"])
                logger.debug(
                    f"语录ID {quote_id} 查看次数增加到 {quote.view_count}", "群聊语录"
                )
        except Exception as e:
            logger.error(
                f"增加语录查看次数失败 - ID: {quote_id}, 错误: {e}", "群聊语录", e=e
            )

    @staticmethod
    async def get_hottest_quotes(group_id: str, limit: int = 10) -> list[Quote]:
        """获取最热门的语录 (按查看次数)"""
        logger.debug(f"开始获取群组 {group_id} 最热门语录，数量: {limit}", "群聊语录")
        try:
            hottest_quotes = (
                await Quote.filter(group_id=group_id)
                .order_by("-view_count")
                .limit(limit)
                .all()
            )

            logger.debug(
                f"成功获取群组 {group_id} 热门语录，共 {len(hottest_quotes)} 条",
                "群聊语录",
            )

            for i, quote in enumerate(hottest_quotes):
                quote_type = (
                    "图片语录"
                    if (
                        quote.image_path and not (quote.ocr_text or quote.recorded_text)
                    )
                    else "文本语录"
                )
                logger.debug(
                    f"热门语录 #{i + 1}: ID={quote.id}, 类型={quote_type}, 查看次数={quote.view_count}",
                    "群聊语录",
                )

            return hottest_quotes
        except Exception as e:
            logger.error(f"获取群组 {group_id} 热门语录失败: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def generate_hottest_quotes_image(
        group_id: str, hottest_quotes: list[Quote], bot_self_id: str
    ) -> bytes | str:
        """使用HTML模板为热门语录列表生成一张汇总图片"""
        if not hottest_quotes:
            return f"群组 {group_id} 暂时没有热门语录。"

        logger.info(f"开始使用HTML模板生成群组 {group_id} 的热门语录图片", "群聊语录")

        quote_cards_data = []

        for i, quote in enumerate(hottest_quotes):
            avatar_base64 = ""
            if quote.quoted_user_id:
                try:
                    avatar_path = await avatar_service.get_avatar_path(
                        platform="qq", identifier=quote.quoted_user_id
                    )
                    if avatar_path:
                        async with aiofiles.open(avatar_path, "rb") as f:
                            avatar_data = await f.read()
                        avatar_base64 = base64.b64encode(avatar_data).decode("utf-8")
                except Exception as e:
                    logger.warning(
                        f"获取用户 {quote.quoted_user_id} 头像失败: {e}", "群聊语录"
                    )

            user_name = ""
            if quote.quoted_user_id:
                user_info = await GroupInfoUser.get_or_none(
                    user_id=quote.quoted_user_id, group_id=quote.group_id
                )
                if user_info:
                    user_name = (
                        user_info.user_name
                        or user_info.nickname
                        or quote.quoted_user_id
                    )
                else:
                    user_name = quote.quoted_user_id

            is_image_quote = quote.image_path and not (
                quote.ocr_text or quote.recorded_text
            )
            preview_text = quote.ocr_text or quote.recorded_text
            if is_image_quote:
                preview_text = "[图片语录]"
            elif not preview_text:
                preview_text = "[未知内容]"

            image_path = ""
            if is_image_quote:
                absolute_path = resolve_quote_image_path(quote.image_path)
                if os.path.exists(absolute_path):
                    image_path = f"file://{absolute_path}"

            card_data = HotQuoteItemData(
                rank=i + 1,
                user_name=user_name,
                avatar_data_url=f"data:image/png;base64,{avatar_base64}"
                if avatar_base64
                else "",
                preview_text=preview_text,
                is_image_quote=is_image_quote,
                image_path=image_path,
                view_count=quote.view_count,
                quote_id=quote.id,
            )
            quote_cards_data.append(card_data)

        page_data = HotQuotesPageData(
            group_id=group_id,
            quotes=quote_cards_data,
        )

        try:
            pic_bytes = await ui.render(page_data)
            logger.info(f"群组 {group_id} 热门语录图片生成成功", "群聊语录")
            return pic_bytes
        except Exception as e:
            logger.error(f"使用HTML模板生成热门语录图片失败: {e}", "群聊语录", e=e)
            return "生成热门语录图片失败，请检查模板文件或日志。"

    @staticmethod
    async def get_most_prolific_uploaders(group_id: str, limit: int = 10) -> list[dict]:
        """获取最高产的语录上传用户"""
        logger.debug(f"获取群组 {group_id} 最高产上传用户，数量: {limit}", "群聊语录")
        prolific_users = (
            await Quote.filter(group_id=group_id, uploader_user_id__not_isnull=True)
            .annotate(upload_count=Count("uploader_user_id"))
            .group_by("uploader_user_id")
            .order_by("-upload_count")
            .limit(limit)
            .values("uploader_user_id", "upload_count")
        )
        return prolific_users

    @staticmethod
    async def get_most_quoted_users(group_id: str, limit: int = 10) -> list[dict]:
        """获取被记录语录最多的用户"""
        logger.debug(f"获取群组 {group_id} 被记录最多用户，数量: {limit}", "群聊语录")
        quoted_users = (
            await Quote.filter(group_id=group_id, quoted_user_id__not_isnull=True)
            .annotate(quote_count=Count("quoted_user_id"))
            .group_by("quoted_user_id")
            .order_by("-quote_count")
            .limit(limit)
            .values("quoted_user_id", "quote_count")
        )
        return quoted_users

    @classmethod
    async def generate_bar_chart_for_prolific_users(
        cls, group_id: str, data: list[dict], title_prefix: str
    ) -> bytes | str:
        """为高产用户生成柱状图"""
        if not data:
            return f"{title_prefix}数据为空"

        user_ids = [
            item.get("uploader_user_id") or item.get("quoted_user_id") for item in data
        ]
        counts = [item.get("upload_count") or item.get("quote_count") for item in data]

        user_names = []
        for uid in user_ids:
            if not uid:
                user_names.append("未知用户")
                continue
            user_info = await GroupInfoUser.get_or_none(
                group_id=group_id, user_id=str(uid)
            )
            user_names.append(
                user_info.user_name if user_info and user_info.user_name else str(uid)
            )

        user_names.reverse()
        counts.reverse()

        barh_data = Barh(
            category_data=user_names,
            data=counts,  # type: ignore
            title=f"群组 {group_id} {title_prefix}排行",
        )
        try:
            chart_image = await ChartUtils.barh(barh_data)
            return chart_image  # type: ignore
        except Exception as e:
            logger.error(f"生成 {title_prefix} 图表失败: {e}", "群聊语录", e=e)
            return f"生成 {title_prefix} 图表失败: {e}"

    @staticmethod
    def cut_sentence(text: str | None) -> list[str]:
        """使用 pkuseg 对文本进行分词，并去除标点符号等无用词"""
        if not text:
            logger.debug("分词文本为空", "群聊语录")
            return []

        cut_words = seg.cut(text)
        cut_words_list: list[str] = list(set(str(word) for word in cut_words))

        punctuation = ".,!?:;。，！？：；%$\n []()（）《》<>「」'''-_+=*&^#@~`"
        stopwords = [
            "的",
            "了",
            "是",
            "在",
            "我",
            "有",
            "和",
            "就",
            "不",
            "人",
            "都",
            "一",
            "一个",
            "上",
            "也",
            "很",
            "到",
            "说",
            "要",
            "去",
            "你",
            "会",
            "着",
            "没有",
            "看",
            "好",
            "自己",
            "这",
        ]
        remove_set = set(punctuation) | set(stopwords)

        new_words: list[str] = [
            word
            for word in cut_words_list
            if word not in remove_set and len(word.strip()) > 0
        ]

        if len(text) <= 10:
            if text.strip() and text.strip() not in remove_set:
                new_words.append(text.strip())

        return new_words
