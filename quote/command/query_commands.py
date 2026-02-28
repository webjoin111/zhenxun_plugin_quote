import os
from pathlib import Path

import aiofiles
from arclet.alconna import Alconna, Args, Arparma, Subcommand, MultiVar
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.typing import T_State
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_alconna.uniseg import UniMessage

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from ..config import (
    safe_file_exists,
    resolve_quote_image_path,
    DATA_PATH,
)
from ..model import Quote
from ..services.quote_service import QuoteService

quote_alc = Alconna("语录", Args["target_user?", At]["search_keywords?", MultiVar(str)])
record_pool = on_alconna(quote_alc, priority=2, block=True)

stats_alc = Alconna(
    "quote",
    Subcommand(
        "stats",
        Subcommand("hot", Args["limit?", int, 10], alias={"热门"}),
        Subcommand("top-uploaders", Args["limit?", int, 10], alias={"高产上传"}),
        Subcommand("top-quoted", Args["limit?", int, 10], alias={"高产被录"}),
    ),
)
quote_stats_cmd = on_alconna(stats_alc, priority=5, block=True)
quote_stats_cmd.shortcut("语录统计", {"args": ["stats"]})


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

    async with aiofiles.open(absolute_path, "rb") as f:
        image_bytes = await f.read()
    message_to_send = MessageUtils.build_message(image_bytes)

    if fallback_message:
        await fallback_message.send(target=target, bot=bot)

    await QuoteService.increment_view_count(quote.id)
    await message_to_send.send(target=target, bot=bot)

    if os.path.isabs(quote.image_path) or "\\" in quote.image_path:
        try:
            fixed_path = quote.image_path.replace("\\", "/")
            if os.path.isabs(fixed_path):
                relative_path = os.path.relpath(fixed_path, DATA_PATH)
            else:
                relative_path = fixed_path

            relative_path = Path(relative_path).as_posix()
            quote.image_path = relative_path
            await quote.save(update_fields=["image_path"])
            logger.info(f"已自动修复语录 {quote.id} 的路径格式: {relative_path}")
        except Exception as e:
            logger.warning(f"惰性迁移语录 {quote.id} 路径失败: {e}")


@quote_stats_cmd.handle()
async def handle_quote_stats(bot: Bot, event: Event, arp: Arparma):
    """语录统计处理函数"""
    session_id = event.get_session_id()
    current_user_id = str(event.get_user_id())

    group_id_to_query: str | None = None

    if "group" in session_id:
        group_id_to_query = session_id.split("_")[1]

    if not group_id_to_query:
        await quote_stats_cmd.finish("请在群聊中执行此命令。")
        return

    reply_target = PlatformUtils.get_target(
        group_id=session_id.split("_")[1] if "group" in session_id else None,
        user_id=current_user_id if "private" in session_id else None,
    )
    if not reply_target:
        reply_target = PlatformUtils.get_target(user_id=current_user_id)

    result_message: str | bytes | None = None

    path_to_check = ""
    if arp.find("stats.hot"):
        path_to_check = "hot"
    elif arp.find("stats.top-uploaders"):
        path_to_check = "top-uploaders"
    elif arp.find("stats.top-quoted"):
        path_to_check = "top-quoted"

    if path_to_check == "hot":
        limit = arp.query("stats.hot.limit", 10)
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

    elif path_to_check == "top-uploaders":
        limit = arp.query("stats.top-uploaders.limit", 10)
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

    elif path_to_check == "top-quoted":
        limit = arp.query("stats.top-quoted.limit", 10)
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
            elif isinstance(result_message, bytes):
                await MessageUtils.build_message(result_message).send(
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
