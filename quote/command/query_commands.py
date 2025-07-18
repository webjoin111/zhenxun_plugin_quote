import os

from arclet.alconna import Alconna, Args, Arparma, Option, Subcommand, MultiVar
from nonebot.adapters.onebot.v11 import Bot, Event, MessageEvent
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_alconna.uniseg import Image, UniMessage
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger
from zhenxun.utils.image_utils import BuildImage
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from ..config import safe_file_exists, resolve_quote_image_path, DATA_PATH
from ..model import Quote
from ..services.quote_service import QuoteService
from ..services.theme_service import theme_service

quote_alc = Alconna("语录", Args["target_user?", At]["search_keywords?", MultiVar(str)])
record_pool = on_alconna(quote_alc, priority=2, block=True)

theme_list_alc = Alconna("语录主题")
theme_list_cmd = on_alconna(theme_list_alc, block=True, priority=5)

alltag_alc = Alconna("alltag")
alltag_cmd = on_alconna(alltag_alc, aliases={"标签", "tag"}, block=True)

stats_alc = Alconna(
    "语录统计",
    Subcommand("热门", Args["limit?", int, 10], help_text="查看热门语录"),
    Subcommand("高产上传", Args["limit?", int, 10], help_text="查看上传最多的用户"),
    Subcommand("高产被录", Args["limit?", int, 10], help_text="查看被记录最多的用户"),
    Option("-g", Args["target_group_id?", str], help_text="指定群组 (仅SUPERUSER可用)"),
)
quote_stats_cmd = on_alconna(stats_alc, priority=5, block=True)


async def _get_valid_quote(
    group_id: str,
    user_id_filter: str | None = None,
    keyword: str | None = None,
    max_retries: int = 3,
) -> Quote | None:
    """
    安全地获取一条有效的语录。
    该函数会检查语录对应的图片文件是否存在，如果不存在，则删除该条记录并尝试重新获取。
    """
    for _ in range(max_retries):
        quote: Quote | None = None
        if keyword:
            quote = await QuoteService.search_quote(group_id, keyword, user_id_filter)
        else:
            quote = await QuoteService.get_random_quote(group_id, user_id_filter)

        if not quote:
            return None

        if safe_file_exists(quote.image_path):
            return quote

        logger.warning(
            f"数据库中的语录 (ID: {quote.id}) 对应的图片文件不存在: {quote.image_path}",
            "群聊语录",
        )
        await quote.delete()
        logger.info(f"已删除无效的语录记录 ID: {quote.id}", "群聊语录")

    logger.error(
        f"在尝试 {max_retries} 次后仍未找到有效的语录文件，放弃操作。", "群聊语录"
    )
    return None


@theme_list_cmd.handle()
async def handle_theme_list(bot: Bot, event: Event):
    """处理"语录主题"命令，列出所有可用的主题。"""
    try:
        themes = theme_service.list_themes()
        if not themes:
            await theme_list_cmd.finish("当前没有可用的语录主题。")

        message_parts = ["可用的语录主题列表："]
        for theme in themes:
            theme_id = theme.get("id", "未知ID")
            name = theme.get("name", "未命名")
            desc = theme.get("description", "无描述")
            message_parts.append(f"\n- {theme_id} ({name})\n  {desc}")

        message_to_send = "\n".join(message_parts)

    except Exception as e:
        logger.error("获取语录主题列表失败", "群聊语录", e=e)
        await theme_list_cmd.finish("获取主题列表失败，请查看后台日志。")
        return

    await theme_list_cmd.finish(message_to_send)


@record_pool.handle()
async def record_pool_handle(bot: Bot, event: Event, arp: Arparma, state: T_State):
    """语录查询处理函数 (重构后)"""
    session_id = event.get_session_id()
    if "group" not in session_id:
        return

    group_id = session_id.split("_")[1]
    target = PlatformUtils.get_target(group_id=group_id)

    at_user_info: At | None = arp.all_matched_args.get("target_user")
    search_keywords: list[str] = arp.all_matched_args.get("search_keywords", [])
    search_key_processed = " ".join(search_keywords) if search_keywords else ""
    user_id_filter: str | None = str(at_user_info.target) if at_user_info else None

    quote: Quote | None = None
    fallback_message: UniMessage | None = None

    if user_id_filter:
        quote = await _get_valid_quote(
            group_id, user_id_filter=user_id_filter, keyword=search_key_processed
        )
        if not quote and search_key_processed:
            fallback_quote = await _get_valid_quote(
                group_id, user_id_filter=user_id_filter
            )
            if fallback_quote:
                quote = fallback_quote
                fallback_message = MessageUtils.build_message(
                    [
                        At(target=user_id_filter, flag="user"),
                        f" 关于 '{search_key_processed}' 的语录没找到哦，这是TA的一条随机语录：\n",
                    ]
                )
            else:
                await MessageUtils.build_message(
                    [At(target=user_id_filter, flag="user"), " 没有任何语录哦~"]
                ).send(target=target, bot=bot)
                return
    else:
        quote = await _get_valid_quote(group_id, keyword=search_key_processed)
        if not quote and search_key_processed:
            fallback_quote = await _get_valid_quote(group_id)
            if fallback_quote:
                quote = fallback_quote
                fallback_message = MessageUtils.build_message(
                    ["当前查询无结果, 为您随机发送。"]
                )
            else:
                await MessageUtils.build_message("当前无语录库").send(
                    target=target, bot=bot
                )
                return

    if not quote:
        await MessageUtils.build_message("当前无语录库").send(target=target, bot=bot)
        return

    absolute_path = resolve_quote_image_path(quote.image_path)
    message_to_send = MessageUtils.build_message(absolute_path)

    if fallback_message:
        await fallback_message.send(target=target, bot=bot)

    await QuoteService.increment_view_count(quote.id)
    await message_to_send.send(target=target, bot=bot)

    if os.path.isabs(quote.image_path):
        try:
            relative_path = os.path.relpath(quote.image_path, DATA_PATH)
            quote.image_path = relative_path
            await quote.save(update_fields=["image_path"])
            logger.info(f"已将语录 {quote.id} 的路径更新为相对路径: {relative_path}")
        except Exception as e:
            logger.warning(f"惰性迁移语录 {quote.id} 路径失败: {e}")


@alltag_cmd.handle()
async def alltag_handle(bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo):
    """查看语录标签处理函数"""
    if not session.group:
        return

    user_id = session.user.id
    group_id = session.group.id

    reply = await reply_fetch(event, bot)
    if not reply or not (image_seg := reply.get(Image, 0)):
        await MessageUtils.build_message(
            [At(target=user_id, flag="user"), " 请回复需要指定语录的图片。"]
        ).send()
        return

    image_basename = os.path.basename(image_seg.id)

    quote = await QuoteService.find_quote_by_basename(group_id, image_basename)
    msg_text = ""
    if not quote:
        msg_text = "该语录不存在"
    else:
        tags = quote.tags
        if tags and len(tags) > 0:
            msg_text = "该语录的所有Tag为: " + " ".join(tags)
        else:
            msg_text = "该语录没有标签"

    await MessageUtils.build_message(
        [At(target=user_id, flag="user"), " " + msg_text]
    ).send()


@quote_stats_cmd.handle()
async def handle_quote_stats(bot: Bot, event: Event, arp: Arparma):
    """语录统计处理函数"""
    session_id = event.get_session_id()
    current_user_id = str(event.get_user_id())

    is_superuser = await SUPERUSER(bot, event)
    target_group_id_opt = arp.query("g.target_group_id", None)

    group_id_to_query: str | None = None

    if target_group_id_opt and is_superuser:
        group_id_to_query = target_group_id_opt
    elif "group" in session_id:
        group_id_to_query = session_id.split("_")[1]

    if not group_id_to_query:
        await quote_stats_cmd.finish(
            "请在群聊中执行此命令，或超级用户使用 -g 指定群号。"
        )
        return

    reply_target = PlatformUtils.get_target(
        group_id=session_id.split("_")[1] if "group" in session_id else None,
        user_id=current_user_id if "private" in session_id else None,
    )
    if not reply_target:
        reply_target = PlatformUtils.get_target(user_id=current_user_id)

    result_message: str | BuildImage | None = None

    raw_command = (
        event.get_plaintext() if hasattr(event, "get_plaintext") else str(event.message)
    )
    logger.debug(f"原始命令文本: {raw_command}", "群聊语录")

    path_to_check = ""
    if "高产被录" in raw_command:
        path_to_check = "高产被录"
    elif "高产上传" in raw_command:
        path_to_check = "高产上传"
    elif "热门" in raw_command:
        path_to_check = "热门"

    if path_to_check == "热门":
        limit = arp.query("热门.limit", 10)
        logger.debug(f"执行 '热门' 统计，limit={limit}", "群聊语录")

        import time

        start_time = time.time()

        hottest_quotes = await QuoteService.get_hottest_quotes(group_id_to_query, limit)

        fetch_time = time.time() - start_time
        logger.debug(f"获取热门语录耗时: {fetch_time:.3f}秒", "群聊语录")

        if hottest_quotes:
            image_quotes_count = sum(
                1
                for q in hottest_quotes
                if q.image_path and not (q.ocr_text or q.recorded_text)
            )
            text_quotes_count = len(hottest_quotes) - image_quotes_count
            logger.debug(
                f"热门语录类型统计 - 总数: {len(hottest_quotes)}, "
                f"图片语录: {image_quotes_count}, 文本语录: {text_quotes_count}",
                "群聊语录",
            )

            gen_start_time = time.time()

            result_message = await QuoteService.generate_hottest_quotes_image(
                group_id_to_query, hottest_quotes, bot.self_id
            )

            gen_time = time.time() - gen_start_time
            logger.debug(f"生成热门语录图片耗时: {gen_time:.3f}秒", "群聊语录")

            total_time = time.time() - start_time
            logger.debug(f"热门语录统计总耗时: {total_time:.3f}秒", "群聊语录")
        else:
            logger.warning(f"群组 {group_id_to_query} 没有热门语录数据", "群聊语录")
            result_message = f"群组 {group_id_to_query} 暂时没有热门语录。"

    elif path_to_check == "高产上传":
        limit = arp.query("高产上传.limit", 10)
        logger.info(f"执行 '高产上传' 统计，limit={limit}", "群聊语录")
        prolific_uploaders = await QuoteService.get_most_prolific_uploaders(
            group_id_to_query, limit
        )
        if prolific_uploaders:
            result_message = await QuoteService.generate_bar_chart_for_prolific_users(
                group_id_to_query, prolific_uploaders, "语录上传"
            )
        else:
            result_message = f"群组 {group_id_to_query} 暂时没有高产上传用户数据。"

    elif path_to_check == "高产被录":
        limit = arp.query("高产被录.limit", 10)
        logger.info(f"执行 '高产被录' 统计，limit={limit}", "群聊语录")
        prolific_quoted = await QuoteService.get_most_quoted_users(
            group_id_to_query, limit
        )
        if prolific_quoted:
            result_message = await QuoteService.generate_bar_chart_for_prolific_users(
                group_id_to_query, prolific_quoted, "被记录语录"
            )
        else:
            result_message = f"群组 {group_id_to_query} 暂时没有高产被记录用户数据。"

    else:
        await MessageUtils.build_message(
            "请指定统计类型：热门、高产上传、高产被录。\n例如：语录统计 热门"
        ).send(target=reply_target, bot=bot)
        return

    try:
        if result_message:
            if isinstance(result_message, str):
                await MessageUtils.build_message(result_message).send(
                    target=reply_target, bot=bot
                )
            elif isinstance(result_message, BuildImage):
                await MessageUtils.build_message(result_message.pic2bytes()).send(
                    target=reply_target, bot=bot
                )
        else:
            await MessageUtils.build_message("无法获取统计数据或数据为空。").send(
                target=reply_target, bot=bot
            )
    except Exception as e:
        logger.error(f"语录统计过程中发送消息发生错误: {e}", "群聊语录", e=e)
        await MessageUtils.build_message(f"统计过程中发生错误: {e}").send(
            target=reply_target, bot=bot
        )
