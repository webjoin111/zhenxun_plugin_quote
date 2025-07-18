import os
import random
from pathlib import Path
from typing import ClassVar, Any
import base64

from nonebot_plugin_htmlrender import template_to_pic
from tortoise.expressions import Q
from tortoise.functions import Count
from nonebot.adapters.onebot.v11 import Bot

from zhenxun.configs.config import Config
from zhenxun.models.group_member_info import GroupInfoUser
from zhenxun.services.log import logger
from zhenxun.services.data_access import DataAccess
from zhenxun.utils.echart_utils import ChartUtils
from zhenxun.utils.echart_utils.models import Barh
from zhenxun.utils.image_utils import BuildImage
from zhenxun.utils.platform import PlatformUtils

from ..config import DATA_PATH, QUOTE_ASSETS_PATH, resolve_quote_image_path
from ..model import Quote
from .theme_service import theme_service

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

quote_dao = DataAccess(Quote)


class QuoteService:
    """语录服务类"""

    _recent_quotes: ClassVar[dict[str, list[int]]] = {}
    _max_memory_size: ClassVar[int] = 10

    @staticmethod
    async def check_duplicate_text_quote(
        group_id: str, recorded_text: str, quoted_user_id: str
    ) -> bool:
        """
        仅检查基于文本的语录是否重复，不执行添加操作。
        返回: True 如果重复，否则 False。
        """
        logger.info(
            f"开始检查重复文本语录 - 群组: {group_id}, 用户: {quoted_user_id}",
            "群聊语录",
        )
        existing_quote = await Quote.filter(
            group_id=group_id,
            recorded_text=recorded_text,
            quoted_user_id=str(quoted_user_id),
        ).first()

        if existing_quote:
            logger.warning(
                f"发现重复文本语录 - 群组: {group_id}, 已存在ID: {existing_quote.id}",
                "群聊语录",
            )
            return True
        return False

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

            query_conditions = []
            if image_hash:
                query_conditions.append(Q(image_hash=image_hash))

            if recorded_text and quoted_user_id:
                query_conditions.append(
                    Q(recorded_text=recorded_text, quoted_user_id=quoted_user_id)
                )

            if query_conditions:
                final_condition = query_conditions[0]
                for condition in query_conditions[1:]:
                    final_condition |= condition

                existing_quote = (
                    await Quote.filter(group_id=group_id)
                    .filter(final_condition)
                    .first()
                )
                if existing_quote:
                    logger.warning(
                        f"发现相似或相同的语录 - 群组: {group_id}, 已存在ID: {existing_quote.id}",
                        "群聊语录",
                    )
                    return existing_quote, False

            tags_source = ocr_content if ocr_content else recorded_text
            tags = QuoteService.cut_sentence(tags_source) if tags_source else []

            try:
                relative_image_path = os.path.relpath(image_path, DATA_PATH)
                logger.debug(
                    f"将绝对路径 '{image_path}' 转换为相对路径 '{relative_image_path}' 进行存储。"
                )
            except ValueError:
                relative_image_path = image_path
                logger.warning(f"无法为 '{image_path}' 计算相对路径，将按原样存储。")

            quote = await quote_dao.create(
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

                await quote_dao.delete(id=quote.id)
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

            count = await quote_dao.count(**query_filters)
            if count == 0:
                logger.info(
                    f"群组 {group_id} 中 (用户: {user_id_filter or '任意'}) 没有语录",
                    "群聊语录",
                )
                return None

            memory_key = f"{group_id}_{user_id_filter or 'all'}"

            quotes = await quote_dao.filter(**query_filters)

            if not quotes:
                return None

            recent_ids = cls._recent_quotes.get(memory_key, [])
            if recent_ids and count > cls._max_memory_size:
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
        1. 在数据库中根据文本内容进行模糊搜索。
        2. 在应用层面对结果进行标签的模糊匹配过滤。
        """
        base_filters = {"group_id": group_id}
        if user_id_filter:
            base_filters["quoted_user_id"] = user_id_filter

        keywords = [k.strip() for k in keyword.split() if k.strip()]
        if not keywords:
            return []

        logger.info(f"两阶段搜索 - 关键词列表: {keywords}", "群聊语录")

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

        candidate_quotes = await quote_dao.filter(text_query_condition, **base_filters)

        logger.debug(
            f"数据库文本搜索初步匹配到 {len(candidate_quotes)} 条语录", "群聊语录"
        )

        if not candidate_quotes:
            return []

        final_matches = []
        for quote in candidate_quotes:
            tags_to_check = quote.tags if isinstance(quote.tags, list) else []
            if not tags_to_check:
                if all(
                    cls._check_single_keyword_in_quote(kw, quote) for kw in keywords
                ):
                    final_matches.append(quote)
                continue

            if all(cls._check_single_keyword_in_quote(kw, quote) for kw in keywords):
                final_matches.append(quote)

        logger.debug(
            f"经过标签过滤后，最终匹配到 {len(final_matches)} 条语录", "群聊语录"
        )
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

            quotes = await quote_dao.filter(
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
    async def get_all_quotes() -> list[Quote]:
        """获取所有语录"""
        try:
            logger.info("开始获取所有语录", "群聊语录")
            quotes = await quote_dao.all()
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

            final_matched_quotes = await quote_dao.filter(query)

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
        text: str,
        author: str,
        save_to_file: bool = False,
        save_path: str | None = None,
        style_name: str | None = None,
    ) -> bytes:
        """生成临时语录图片"""
        try:
            final_style_name = style_name

            if final_style_name:
                try:
                    theme_service.get_theme(final_style_name)
                    logger.info(f"使用用户指定主题: {final_style_name}", "群聊语录")
                except ValueError:
                    logger.warning(
                        f"用户指定的主题 '{final_style_name}' 不存在，将使用随机主题。"
                    )
                    final_style_name = None

            if not final_style_name:
                theme_pool = Config.get_config("quote", "QUOTE_THEME", ["classic"])

                if "all" in theme_pool:
                    all_themes_info = theme_service.list_themes()
                    theme_pool = [theme["id"] for theme in all_themes_info]

                available_themes = [t for t in theme_pool if t in theme_service._themes]

                if not available_themes:
                    logger.warning(
                        "配置的主题池为空或所有主题均无效，将使用后备默认主题 'classic'。"
                    )
                    final_style_name = "classic"
                else:
                    final_style_name = random.choice(available_themes)

            logger.info(
                f"开始生成临时语录图片 - 作者: {author}, 主题: {final_style_name}",
                "群聊语录",
            )

            from ..services.image_service import ImageService

            img_data = await ImageService.generate_quote(
                avatar_bytes=avatar_bytes,
                text=text,
                author=author,
                style_name=final_style_name,
            )

            if save_to_file:
                import hashlib
                import aiofiles
                from ..config import quote_path

                image_name = hashlib.md5(img_data).hexdigest() + ".png"
                if save_path:
                    image_path = Path(save_path)
                else:
                    from ..config import ensure_quote_path

                    ensure_quote_path()
                    image_path = quote_path / image_name

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
            quote = await quote_dao.get_or_none(id=quote_id)
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
    ) -> BuildImage | str:
        """使用HTML模板为热门语录列表生成一张汇总图片"""
        if not hottest_quotes:
            return f"群组 {group_id} 暂时没有热门语录。"

        logger.info(f"开始使用HTML模板生成群组 {group_id} 的热门语录图片", "群聊语录")

        quote_cards_data = []

        for i, quote in enumerate(hottest_quotes):
            avatar_base64 = ""
            if quote.quoted_user_id:
                try:
                    avatar_data = await PlatformUtils.get_user_avatar(
                        quote.quoted_user_id, "qq", bot_self_id
                    )
                    if avatar_data:
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

            card_data = {
                "rank": i + 1,
                "user_name": user_name,
                "avatar_data_url": f"data:image/png;base64,{avatar_base64}"
                if avatar_base64
                else "",
                "preview_text": preview_text,
                "is_image_quote": is_image_quote,
                "image_path": image_path,
                "view_count": quote.view_count,
                "quote_id": quote.id,
            }
            quote_cards_data.append(card_data)

        template_data = {
            "group_id": group_id,
            "quotes": quote_cards_data,
        }

        try:
            template_path = QUOTE_ASSETS_PATH / "templates"
            template_name = "hot_quotes.html"

            pic_bytes = await template_to_pic(
                template_path=str(template_path.resolve()),
                template_name=template_name,
                templates=template_data,
                pages={
                    "viewport": {"width": 800, "height": 10},
                    "base_url": f"file://{template_path.resolve()}",
                },
                wait=0,
            )

            logger.info(f"群组 {group_id} 热门语录图片生成成功", "群聊语录")
            return BuildImage.open(pic_bytes)

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

        return new_words
