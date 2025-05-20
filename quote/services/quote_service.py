import random
from typing import ClassVar

import spacy_pkuseg as pkuseg
from tortoise.expressions import Q
from tortoise.functions import Count

from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services.log import logger
from zhenxun.utils.echart_utils import ChartUtils
from zhenxun.utils.echart_utils.models import Barh
from zhenxun.utils.image_utils import BuildImage, text2image
from zhenxun.utils.platform import PlatformUtils

from ..config import get_font_path
from ..model import Quote

seg = pkuseg.pkuseg(model_name="web")


class QuoteService:
    """语录服务类"""

    _recent_quotes: ClassVar[dict[str, list[int]]] = {}
    _max_memory_size: ClassVar[int] = 10

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
        """向数据库添加语录

        参数:
            group_id: 群组ID
            image_path: 图片路径
            ocr_content: OCR识别文本
            recorded_text: 记录的文本
            quoted_user_id: 被记录用户的QQ号
            image_hash: 图片哈希值，如果为None则会自动计算
            uploader_user_id: 上传者ID

        返回:
            tuple[Quote | None, bool]:
                - 第一个元素: Quote对象或None（失败时）
                - 第二个元素: 是否为新添加的语录（True）或重复语录（False）
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
                        f"发现相似图片 - 群组: {group_id}, 已存在ID: {existing_quote.id}, 路径: {existing_quote.image_path}",
                        "群聊语录",
                    )
                    return existing_quote, False

            if recorded_text and quoted_user_id:
                existing_text_quote = await Quote.filter(
                    group_id=group_id,
                    recorded_text=recorded_text,
                    quoted_user_id=quoted_user_id,
                ).first()

                if existing_text_quote:
                    logger.warning(
                        f"发现相同文本和被记录用户的语录 - 群组: {group_id}, 已存在ID: {existing_text_quote.id}",
                        "群聊语录",
                    )
                    return existing_text_quote, False

            tags_source = ocr_content if ocr_content else recorded_text
            tags = QuoteService.cut_sentence(tags_source) if tags_source else []

            quote, created = await Quote.get_or_create(
                image_path=image_path,
                defaults={
                    "group_id": group_id,
                    "image_hash": image_hash,
                    "ocr_text": ocr_content,
                    "recorded_text": recorded_text,
                    "tags": tags,
                    "quoted_user_id": quoted_user_id,
                    "uploader_user_id": uploader_user_id,
                    "view_count": 0,
                },
            )

            if created:
                logger.info(
                    f"语录添加成功 - ID: {quote.id}, 群组: {group_id}", "群聊语录"
                )
                return quote, True
            else:
                logger.warning(f"语录已存在 - 路径: {image_path}", "群聊语录")
                return quote, False
        except Exception as e:
            logger.error(f"添加语录失败 - 群组: {group_id}, 错误: {e}", "群聊语录", e=e)
            return None, False

    @staticmethod
    async def delete_quote(group_id: str, image_basename: str) -> bool:
        """从数据库删除语录"""
        try:
            logger.info(
                f"尝试删除语录 - 群组: {group_id}, 图片: {image_basename}", "群聊语录"
            )
            quote = await QuoteService.find_quote_by_basename(group_id, image_basename)
            if quote:
                await quote.delete()
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

            if count <= cls._max_memory_size:
                logger.info(
                    f"语录数量 ({count}) 少于或等于记忆大小 ({cls._max_memory_size}) for {memory_key}，无法完全避免重复",
                    "群聊语录",
                )
                quotes = await Quote.filter(**query_filters).all()
                quote = cls._select_and_record_quote(memory_key, quotes)
            else:
                recent_ids = cls._recent_quotes.get(memory_key, [])
                if recent_ids:
                    logger.info(
                        f"最近展示过的语录ID ({memory_key}): {recent_ids}，将避免重复",
                        "群聊语录",
                    )
                    quotes = (
                        await Quote.filter(**query_filters)
                        .exclude(id__in=recent_ids)
                        .all()
                    )
                    if not quotes:
                        logger.warning(
                            f"所有语录 ({memory_key}) 都已展示过，将重置记忆",
                            "群聊语录",
                        )
                        cls._recent_quotes[memory_key] = []
                        quotes = await Quote.filter(**query_filters).all()
                        quote = cls._select_and_record_quote(memory_key, quotes)
                    else:
                        quote = cls._select_and_record_quote(memory_key, quotes)
                else:
                    quotes = await Quote.filter(**query_filters).all()
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
    async def search_quote(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> Quote | None:
        """根据关键词搜索语录，可根据用户筛选"""
        logger.info(
            f"开始搜索语录 - 群组: {group_id}, 关键词: {keyword}, 用户筛选: {user_id_filter}",
            "群聊语录",
        )
        keyword_lower = keyword.lower()
        memory_key = f"{group_id}_{user_id_filter or 'all'}"

        try:
            base_query_filters = {"group_id": group_id}
            if user_id_filter:
                base_query_filters["quoted_user_id"] = user_id_filter

            count = await Quote.filter(**base_query_filters).count()
            if count == 0:
                logger.info(
                    f"群组 {group_id} (用户: {user_id_filter or '任意'}) 中没有语录。",
                    "群聊语录",
                )
                return None

            exact_text_matches = []
            exact_ocr_matches = await Quote.filter(
                **base_query_filters, ocr_text__iexact=keyword
            ).all()
            exact_text_matches.extend(exact_ocr_matches)
            exact_recorded_matches = await Quote.filter(
                **base_query_filters, recorded_text__iexact=keyword
            ).all()
            exact_text_matches.extend(exact_recorded_matches)

            if exact_text_matches:
                logger.info(
                    f"找到精确文本匹配的语录 {len(exact_text_matches)} 条", "群聊语录"
                )
                random_quote = cls._select_and_record_quote(
                    memory_key, exact_text_matches
                )
                logger.info(
                    f"搜索到语录 ID: {random_quote.id} (路径: {random_quote.image_path})",
                    "群聊语录",
                )
                return random_quote

            quotes_with_tags_query = Quote.filter(**base_query_filters)
            quotes_with_tags = await quotes_with_tags_query.values("id", "tags")
            exact_tag_match_ids = [
                quote["id"]
                for quote in quotes_with_tags
                if any(keyword_lower == tag.lower() for tag in quote["tags"])
            ]
            if exact_tag_match_ids:
                exact_tag_matches = await Quote.filter(id__in=exact_tag_match_ids).all()
                logger.info(
                    f"找到精确标签匹配的语录 {len(exact_tag_matches)} 条", "群聊语录"
                )
                random_quote = cls._select_and_record_quote(
                    memory_key, exact_tag_matches
                )
                logger.info(
                    f"搜索到语录 ID: {random_quote.id} (路径: {random_quote.image_path})",
                    "群聊语录",
                )
                return random_quote

            query_condition_text = Q(ocr_text__icontains=keyword) | Q(
                recorded_text__icontains=keyword
            )
            partial_text_matches = (
                await Quote.filter(**base_query_filters)
                .filter(query_condition_text)
                .all()
            )
            if partial_text_matches:
                logger.info(
                    f"找到部分文本匹配的语录 {len(partial_text_matches)} 条", "群聊语录"
                )
                random_quote = cls._select_and_record_quote(
                    memory_key, partial_text_matches
                )
                logger.info(
                    f"搜索到语录 ID: {random_quote.id} (路径: {random_quote.image_path})",
                    "群聊语录",
                )
                return random_quote

            partial_tag_match_ids = [
                quote["id"]
                for quote in quotes_with_tags
                if any(keyword_lower in tag.lower() for tag in quote["tags"])
            ]
            if partial_tag_match_ids:
                partial_tag_matches = await Quote.filter(
                    id__in=partial_tag_match_ids
                ).all()
                logger.info(
                    f"找到部分标签匹配的语录 {len(partial_tag_matches)} 条", "群聊语录"
                )
                random_quote = cls._select_and_record_quote(
                    memory_key, partial_tag_matches
                )
                logger.info(
                    f"搜索到语录 ID: {random_quote.id} (路径: {random_quote.image_path})",
                    "群聊语录",
                )
                return random_quote

            keyword_tokens = QuoteService.cut_sentence(keyword)
            if not keyword_tokens:
                logger.info(
                    f"分词结果为空，(用户: {user_id_filter or '任意'}) 中未找到与 '{keyword}' 相关的语录。",
                    "群聊语录",
                )
                return None

            logger.debug(f"搜索关键词分词结果: {keyword_tokens}", "群聊语录")

            token_queries = []
            for token in keyword_tokens:
                token_lower = token.lower()
                token_queries.append(
                    Q(ocr_text__icontains=token_lower)
                    | Q(recorded_text__icontains=token_lower)
                )

            token_query = token_queries[0]
            for query_part in token_queries[1:]:
                token_query = token_query | query_part

            token_text_matches = (
                await Quote.filter(**base_query_filters).filter(token_query).all()
            )

            token_tag_match_ids = []
            for quote_tags_data in quotes_with_tags:
                for token in keyword_tokens:
                    token_lower = token.lower()
                    if any(
                        token_lower in tag.lower() for tag in quote_tags_data["tags"]
                    ):
                        token_tag_match_ids.append(quote_tags_data["id"])
                        break

            token_tag_matches = []
            if token_tag_match_ids:
                token_tag_matches = await Quote.filter(id__in=token_tag_match_ids).all()

            token_matches = list(set(token_text_matches + token_tag_matches))

            if token_matches:
                logger.info(f"找到分词匹配的语录 {len(token_matches)} 条", "群聊语录")
                random_quote = cls._select_and_record_quote(memory_key, token_matches)
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
                f"搜索语录时发生错误 - 群组: {group_id}, 关键词: {keyword}, 用户筛选: {user_id_filter}, 错误: {e}",
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

            quote = await Quote.filter(
                group_id=group_id, image_path__contains=image_basename
            ).first()

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
                f"根据文件名查找语录时发生错误 - 群组: {group_id}, "
                f"文件名: {image_basename}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    async def check_duplicate_by_hash(group_id: str, image_hash: str) -> Quote | None:
        """检查图片哈希是否重复"""
        try:
            if not image_hash:
                return None

            logger.info(
                f"检查重复图片 - 群组: {group_id}, 哈希值: {image_hash}", "群聊语录"
            )

            existing_quote = await Quote.filter(
                group_id=group_id, image_hash=image_hash
            ).first()

            if existing_quote:
                logger.warning(
                    f"发现相似图片 - 群组: {group_id}, 已存在ID: {existing_quote.id}, 路径: {existing_quote.image_path}",
                    "群聊语录",
                )
                return existing_quote

            return None
        except Exception as e:
            logger.error(
                f"检查重复图片失败 - 群组: {group_id}, 哈希值: {image_hash}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return None

    @staticmethod
    async def check_duplicate_by_text(
        group_id: str, recorded_text: str, quoted_user_id: str
    ) -> Quote | None:
        """检查文本和用户是否重复"""
        try:
            if not recorded_text or not quoted_user_id:
                return None

            logger.info(
                f"检查重复文本 - 群组: {group_id}, 被记录用户: {quoted_user_id}",
                "群聊语录",
            )

            existing_quote = await Quote.filter(
                group_id=group_id,
                recorded_text=recorded_text,
                quoted_user_id=quoted_user_id,
            ).first()

            if existing_quote:
                logger.warning(
                    f"发现相同文本和被记录用户的语录 - 群组: {group_id}, 已存在ID: {existing_quote.id}",
                    "群聊语录",
                )
                return existing_quote

            return None
        except Exception as e:
            logger.error(
                f"检查重复文本失败 - 群组: {group_id}, 被记录用户: {quoted_user_id}, 错误: {e}",
                "群聊语录",
                e=e,
            )
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

        recent_ids = cls._recent_quotes.get(memory_key, [])
        unseen_quotes = [q for q in quotes if q.id not in recent_ids]

        if unseen_quotes:
            selected_quote = random.choice(unseen_quotes)
        else:
            selected_quote = random.choice(quotes)

        if memory_key not in cls._recent_quotes:
            cls._recent_quotes[memory_key] = []

        cls._recent_quotes[memory_key].append(selected_quote.id)

        if len(cls._recent_quotes[memory_key]) > cls._max_memory_size:
            cls._recent_quotes[memory_key].pop(0)

        return selected_quote

    @classmethod
    async def search_quotes_for_deletion(
        cls, group_id: str, keyword: str, user_id_filter: str | None = None
    ) -> list[Quote]:
        """根据关键词搜索语录，用于批量删除，可根据用户筛选"""
        logger.info(
            f"开始搜索语录用于删除 - 群组: {group_id}, 关键词: {keyword}, 用户筛选: {user_id_filter}",
            "群聊语录",
        )
        keyword_lower = keyword.lower()
        matched_quotes_set = set()

        try:
            base_query_filters = {"group_id": group_id}
            if user_id_filter:
                base_query_filters["quoted_user_id"] = user_id_filter

            count = await Quote.filter(**base_query_filters).count()
            if count == 0:
                logger.info(
                    f"群组 {group_id} (用户: {user_id_filter or '任意'}) 中没有语录。",
                    "群聊语录",
                )
                return []

            query_condition_text = Q(ocr_text__icontains=keyword) | Q(
                recorded_text__icontains=keyword
            )
            text_matches = (
                await Quote.filter(**base_query_filters)
                .filter(query_condition_text)
                .all()
            )
            for q in text_matches:
                matched_quotes_set.add(q)

            quotes_with_tags_query = Quote.filter(**base_query_filters)
            quotes_with_tags = await quotes_with_tags_query.values("id", "tags")
            tag_match_ids = {
                quote["id"]
                for quote in quotes_with_tags
                if any(keyword_lower in tag.lower() for tag in quote["tags"])
            }
            if tag_match_ids:
                new_tag_match_ids = tag_match_ids - {q.id for q in matched_quotes_set}
                if new_tag_match_ids:
                    tag_matches = await Quote.filter(id__in=new_tag_match_ids).all()
                    for q in tag_matches:
                        matched_quotes_set.add(q)

            if not matched_quotes_set:
                keyword_tokens = cls.cut_sentence(keyword)
                if keyword_tokens:
                    token_queries_text = []
                    for token in keyword_tokens:
                        token_lower = token.lower()
                        token_queries_text.append(
                            Q(ocr_text__icontains=token_lower)
                            | Q(recorded_text__icontains=token_lower)
                        )
                    if token_queries_text:
                        final_token_query_text = token_queries_text[0]
                        for tq in token_queries_text[1:]:
                            final_token_query_text |= tq
                        token_text_matches_qs = (
                            await Quote.filter(**base_query_filters)
                            .filter(final_token_query_text)
                            .all()
                        )
                        for q in token_text_matches_qs:
                            matched_quotes_set.add(q)

                    token_tag_match_ids_token = set()
                    for quote_tags_data in quotes_with_tags:
                        if quote_tags_data["id"] in {q.id for q in matched_quotes_set}:
                            continue
                        for token in keyword_tokens:
                            token_lower = token.lower()
                            if any(
                                token_lower in tag.lower()
                                for tag in quote_tags_data["tags"]
                            ):
                                token_tag_match_ids_token.add(quote_tags_data["id"])
                                break
                    if token_tag_match_ids_token:
                        new_token_tag_ids = token_tag_match_ids_token - {
                            q.id for q in matched_quotes_set
                        }
                        if new_token_tag_ids:
                            token_tag_matches_qs = await Quote.filter(
                                id__in=new_token_tag_ids
                            ).all()
                            for q in token_tag_matches_qs:
                                matched_quotes_set.add(q)

            final_matched_quotes = list(matched_quotes_set)
            logger.info(
                f"找到 {len(final_matched_quotes)} 条与关键词 '{keyword}' 相关的语录 (用户: {user_id_filter or '任意'})",
                "群聊语录",
            )
            return final_matched_quotes

        except Exception as e:
            logger.error(
                f"搜索语录用于删除时发生错误 - 群组: {group_id}, 关键词: {keyword}, 用户筛选: {user_id_filter}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return []

    @staticmethod
    async def generate_temp_quote(
        avatar_bytes: bytes,
        text: str,
        author: str,
        font_path: str,
        author_font_path: str,
        save_to_file: bool = False,
        save_path: str | None = None,
    ) -> bytes:
        """生成临时语录图片"""
        try:
            import hashlib
            import os

            import aiofiles

            logger.info(
                f"开始生成临时语录图片 - 作者: {author}, 文本长度: {len(text)}",
                "群聊语录",
            )

            from ..services.image_service import ImageService

            img_data = await ImageService.generate_quote(
                avatar_bytes=avatar_bytes,
                text=text,
                author=author,
                font_path=font_path,
                author_font_path=author_font_path,
            )

            if save_to_file:
                from ..config import quote_path

                image_name = hashlib.md5(img_data).hexdigest() + ".png"
                if save_path:
                    image_path = save_path
                else:
                    image_path = os.path.abspath(os.path.join(quote_path, image_name))

                async with aiofiles.open(image_path, "wb") as file:
                    await file.write(img_data)

                logger.info(f"临时语录图片已保存到 {image_path}", "群聊语录")

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
                    f"热门语录 #{i + 1}: ID={quote.id}, 类型={quote_type}, "
                    f"查看次数={quote.view_count}",
                    "群聊语录",
                )

            return hottest_quotes
        except Exception as e:
            logger.error(f"获取群组 {group_id} 热门语录失败: {e}", "群聊语录", e=e)
            return []

    @staticmethod
    async def generate_hottest_quotes_image(
        group_id: str, hottest_quotes: list[Quote], bot_self_id: str
    ) -> BuildImage | str:
        """为热门语录列表生成一张汇总图片"""
        if not hottest_quotes:
            return f"群组 {group_id} 暂时没有热门语录。"

        card_width = 700
        card_padding = 20
        avatar_size = 80
        text_area_width = card_width - avatar_size - card_padding * 3

        quote_cards = []

        try:
            font_path = get_font_path()
            logger.debug(f"尝试加载字体，路径: {font_path}", "群聊语录")
            title_font = BuildImage.load_font(font_path, 28)
            text_font = BuildImage.load_font(font_path, 22)
            count_font = BuildImage.load_font(font_path, 20)
            id_font = BuildImage.load_font(font_path, 18)
            logger.debug(f"成功加载字体: {font_path}", "群聊语录")
        except Exception as e:
            logger.error(
                f"加载热门语录图片所需字体失败，路径: {font_path if 'font_path' in locals() else '未知'}, 错误: {e}",
                "群聊语录",
                e=e,
            )
            return "生成热门语录图片失败：字体加载错误。"

        for i, quote in enumerate(hottest_quotes):
            rank = i + 1
            user_name = ""
            if hasattr(quote, "quoted_user_id") and quote.quoted_user_id:
                try:
                    user_info = await GroupInfoUser.get_or_none(
                        user_id=quote.quoted_user_id, group_id=quote.group_id
                    )
                    if user_info and user_info.user_name:
                        user_name = user_info.user_name
                    else:
                        user_name = str(quote.quoted_user_id)
                except Exception as e:
                    logger.warning(f"获取用户信息失败: {e}", "群聊语录")
                    user_name = str(quote.quoted_user_id)

            if quote.image_path and not (quote.ocr_text or quote.recorded_text):
                preview_text = "[图片语录]"
            else:
                raw_text = quote.ocr_text or quote.recorded_text or "[未知内容]"

                def remove_username_prefix(text: str, username: str) -> str:
                    """从文本中移除用户名前缀"""
                    if not username or not text:
                        return text

                    patterns = [
                        f"{username}: ",
                        f"{username}：",
                        f"{username} ",
                        f"[{username}]",
                        f"【{username}】",
                    ]

                    for pattern in patterns:
                        if text.startswith(pattern):
                            return text[len(pattern) :]

                    return text

                if user_name:
                    processed_text = remove_username_prefix(raw_text, user_name)
                    if processed_text != raw_text:
                        logger.debug(
                            f"从语录开头移除用户名 '{user_name}' - ID: {quote.id}",
                            "群聊语录",
                        )
                        preview_text = processed_text
                    else:
                        preview_text = raw_text
                else:
                    preview_text = raw_text
            max_preview_lines = 3
            max_preview_width = text_area_width - 20

            def truncate_text_to_fit(
                text: str, max_width: int, max_lines: int, font
            ) -> str:
                """根据宽度和行数限制截断文本"""
                if not text:
                    return text

                lines = text.split("\n")

                if len(lines) > max_lines:
                    return "\n".join(lines[:max_lines]) + "..."

                result_lines = []
                for line in lines[:max_lines]:
                    current_line = ""
                    for char in line:
                        test_line = current_line + char
                        width, _ = BuildImage.get_text_size(test_line, font)

                        if width > max_width:
                            current_line += "..."
                            break
                        else:
                            current_line = test_line

                    result_lines.append(current_line)

                    if len(result_lines) >= max_lines:
                        break

                return "\n".join(result_lines)

            try:
                preview_text = truncate_text_to_fit(
                    preview_text, max_preview_width, max_preview_lines, text_font
                )
            except Exception as e:
                logger.warning(f"精确截断文本失败，使用简单方法: {e}", "群聊语录")
                font_size = text_font.size if text_font else 22
                chars_per_line = max_preview_width // font_size
                actual_lines = preview_text.split("\n")
                if len(actual_lines) > max_preview_lines:
                    preview_text = "\n".join(actual_lines[:max_preview_lines]) + "..."
                elif len(preview_text) > chars_per_line * max_preview_lines:
                    preview_text = (
                        preview_text[: chars_per_line * max_preview_lines - 10] + "..."
                    )

            avatar_img = None
            if quote.quoted_user_id:
                try:
                    avatar_data = await PlatformUtils.get_user_avatar(
                        quote.quoted_user_id, "qq", bot_self_id
                    )
                    if avatar_data:
                        avatar_img = BuildImage.open(avatar_data)
                        await avatar_img.resize(width=avatar_size, height=avatar_size)
                        await avatar_img.circle()
                except Exception as e:
                    logger.warning(
                        f"获取用户 {quote.quoted_user_id} 头像失败: {e}", "群聊语录"
                    )

            if not avatar_img:
                avatar_img = BuildImage(avatar_size, avatar_size, color=(200, 200, 200))
                await avatar_img.text(
                    (0, 0),
                    "?",
                    font=title_font,
                    center_type="center",
                    fill=(255, 255, 255),
                )
                await avatar_img.circle()

            try:
                id_text_h = BuildImage.get_text_size(f"ID: {quote.id}", id_font)[1]
                rank_text_h = BuildImage.get_text_size(f"No.{rank}", title_font)[1]
                count_text_h = BuildImage.get_text_size(
                    f"查看: {quote.view_count}", count_font
                )[1]
            except Exception as e:
                logger.warning(f"获取文本尺寸失败，使用默认值: {e}", "群聊语录")
                id_text_h = 18
                rank_text_h = 28
                count_text_h = 20

            try:
                temp_preview_img = await text2image(
                    preview_text,
                    font=get_font_path(),
                    font_size=22,
                    auto_parse=False,
                    padding=0,
                )
                preview_text_img_h = temp_preview_img.height
            except Exception as e:
                logger.warning(f"获取预览文本高度失败，使用估算值: {e}", "群聊语录")
                preview_lines_count = len(preview_text.split("\n"))
                preview_text_img_h = max(preview_lines_count, 3) * 22 * 1.2

            max_preview_height = 22 * max_preview_lines * 1.5
            if preview_text_img_h > max_preview_height:
                preview_text_img_h = max_preview_height

            if quote.image_path and not (quote.ocr_text or quote.recorded_text):
                min_image_height = 120
                if preview_text_img_h < min_image_height:
                    preview_text_img_h = min_image_height
                    logger.debug(
                        f"图片语录高度调整为最小值: {min_image_height}px", "群聊语录"
                    )

            card_height = (
                card_padding * 4
                + max(avatar_size, int(preview_text_img_h))
                + rank_text_h
                + count_text_h
                + id_text_h
                + 20
            )

            card = BuildImage(card_width, int(card_height), color=(245, 245, 245))

            await card.text(
                (card_padding, card_padding),
                f"No.{rank}",
                font=title_font,
                fill=(0, 0, 0),
            )
            id_text_w = BuildImage.get_text_size(f"ID: {quote.id}", id_font)[0]
            await card.text(
                (
                    card_width - card_padding - id_text_w,
                    card_padding + (rank_text_h - id_text_h) // 2,
                ),
                f"ID: {quote.id}",
                font=id_font,
                fill=(100, 100, 100),
            )

            avatar_y = card_padding * 2 + rank_text_h
            await card.paste(avatar_img, (card_padding, avatar_y))

            if user_name:
                user_name_font = BuildImage.load_font(get_font_path(), 18)
                user_name_w = BuildImage.get_text_size(user_name, user_name_font)[0]

                if user_name_w > avatar_size:
                    max_chars = int(avatar_size / (user_name_font.size / 2))
                    if len(user_name) > max_chars:
                        user_name = user_name[: max_chars - 2] + ".."
                    user_name_w = BuildImage.get_text_size(user_name, user_name_font)[0]

                user_name_x = card_padding + (avatar_size - user_name_w) // 2
                user_name_y = avatar_y + avatar_size + 5

                await card.text(
                    (user_name_x, user_name_y),
                    user_name,
                    font=user_name_font,
                    fill=(0, 0, 0),
                )

            preview_x = card_padding * 2 + avatar_size
            preview_y = avatar_y

            if quote.image_path and not (quote.ocr_text or quote.recorded_text):
                try:
                    from os.path import exists

                    if exists(quote.image_path):
                        original_img = BuildImage.open(quote.image_path)

                        preview_img = original_img.copy()

                        original_width = original_img.width
                        original_height = original_img.height

                        width_ratio = text_area_width / original_width
                        height_ratio = preview_text_img_h / original_height

                        scale_ratio = min(width_ratio, height_ratio)

                        new_width = int(original_width * scale_ratio)
                        new_height = int(original_height * scale_ratio)

                        new_width = max(1, new_width)
                        new_height = max(1, new_height)

                        await preview_img.resize(width=new_width, height=new_height)
                    else:
                        logger.error(f"图片文件不存在: {quote.image_path}", "群聊语录")
                        raise FileNotFoundError(f"图片文件不存在: {quote.image_path}")
                except Exception as e:
                    logger.warning(f"加载图片语录缩略图失败: {e}", "群聊语录")
                    try:
                        preview_img = await text2image(
                            preview_text,
                            font=get_font_path(),
                            font_size=22,
                            font_color=(50, 50, 50),
                            color=(245, 245, 245),
                            auto_parse=False,
                            padding=10,
                        )
                    except Exception as text_error:
                        logger.warning(
                            f"生成图片语录文本预览失败: {text_error}", "群聊语录"
                        )
                        preview_img = BuildImage(
                            text_area_width,
                            int(preview_text_img_h),
                            color=(245, 245, 245),
                        )
                        try:
                            await preview_img.text(
                                (0, 0),
                                "[图片语录]",
                                font=text_font,
                                fill=(100, 100, 100),
                                center_type="center",
                            )
                        except Exception as text_error:
                            logger.warning(
                                f"绘制图片语录文本失败: {text_error}", "群聊语录"
                            )
                            pass
            else:
                try:
                    preview_img = await text2image(
                        preview_text,
                        font=get_font_path(),
                        font_size=22,
                        font_color=(50, 50, 50),
                        color=(245, 245, 245),
                        auto_parse=False,
                        padding=10,
                    )

                    if preview_img.width > text_area_width:
                        await preview_img.crop(
                            (0, 0, text_area_width, preview_img.height)
                        )
                except Exception as e:
                    logger.warning(f"使用 text2image 生成预览图片失败: {e}", "群聊语录")
                    preview_img = BuildImage(
                        text_area_width, int(preview_text_img_h), color=(245, 245, 245)
                    )
                    try:
                        await preview_img.text(
                            (0, 0),
                            "无法显示文本预览",
                            font=text_font,
                            fill=(100, 100, 100),
                            center_type="center",
                        )
                    except Exception as text_error:
                        logger.warning(f"绘制预览文本失败: {text_error}", "群聊语录")

            paste_x = preview_x
            paste_y = preview_y

            if quote.image_path and not (quote.ocr_text or quote.recorded_text):
                if preview_img.width < text_area_width:
                    paste_x = preview_x + (text_area_width - preview_img.width) // 2

                if preview_img.height < preview_text_img_h:
                    paste_y = (
                        preview_y + (int(preview_text_img_h) - preview_img.height) // 2
                    )

            await card.paste(preview_img, (paste_x, paste_y))

            view_count_text = f"查看: {quote.view_count}次"
            view_count_w, view_count_h = BuildImage.get_text_size(
                view_count_text, count_font
            )
            view_count_x = card_width - card_padding - view_count_w
            view_count_y = card_height - card_padding - view_count_h
            await card.text(
                (view_count_x, view_count_y),
                view_count_text,
                font=count_font,
                fill=(150, 150, 150),
            )

            await card.circle_corner(15)
            quote_cards.append(card)

        if not quote_cards:
            return f"群组 {group_id} 热门语录数据为空或无法生成卡片。"

        num_columns = 1 if len(quote_cards) <= 3 else 2
        if len(quote_cards) == 1:
            num_columns = 1

        final_image = await BuildImage.auto_paste(
            quote_cards, row=num_columns, space=20, padding=30, color=(230, 230, 230)
        )

        overall_title_text = f"群组 {group_id} 热门语录排行"
        overall_title_font = BuildImage.load_font(get_font_path(), 40)

        try:
            _, title_h = BuildImage.get_text_size(
                overall_title_text, overall_title_font
            )
        except Exception as e:
            logger.warning(f"获取标题尺寸失败，使用默认值: {e}", "群聊语录")
            title_h = 50

        final_image_with_title = BuildImage(
            final_image.width, final_image.height + title_h + 40, color=(230, 230, 230)
        )
        await final_image_with_title.text(
            (0, 20),
            overall_title_text,
            font=overall_title_font,
            fill=(0, 0, 0),
            center_type="width",
        )
        await final_image_with_title.paste(final_image, (0, title_h + 40))

        return final_image_with_title

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
    ) -> BuildImage | str:
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
            data=counts,
            title=f"群组 {group_id} {title_prefix}排行",
        )
        try:
            chart_image = await ChartUtils.barh(barh_data)
            return chart_image
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
        cut_words = list(set(cut_words))

        remove_set = {
            ".",
            ",",
            "!",
            "?",
            ":",
            ";",
            "。",
            "，",
            "！",
            "？",
            "：",
            "；",
            "%",
            "$",
            "\n",
            " ",
            "[",
            "]",
            "(",
            ")",
            "（",
            "）",
            "《",
            "》",
            "<",
            ">",
            "「",
            "」",
            """, """,
            "'",
            "'",
            "-",
            "_",
            "+",
            "=",
            "*",
            "&",
            "^",
            "#",
            "@",
            "~",
            "`",
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
        }

        new_words = [
            word
            for word in cut_words
            if word not in remove_set and len(word.strip()) > 0
        ]

        if len(text) <= 10:
            if text.strip() and text.strip() not in remove_set:
                new_words.append(text.strip())

        logger.debug(
            f"分词结果 - 原始文本长度: {len(text)}, 分词数: {len(new_words)}",
            "群聊语录",
        )
        return new_words
