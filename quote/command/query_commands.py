from pathlib import Path

from arclet.alconna import Alconna, Args, Arparma, Option, Subcommand, MultiVar
from nonebot.adapters.onebot.v11 import Bot, Event, MessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_alconna.uniseg import UniMessage

from zhenxun.services.log import logger
from zhenxun.utils.image_utils import BuildImage
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils

from ..config import safe_file_exists
from ..services.quote_service import QuoteService
from ..utils.exceptions import ReplyImageNotFoundException
from ..utils.message_utils import (
    extract_image_basename_from_reply,
    get_group_id_from_session,
)

quote_alc = Alconna("语录", Args["target_user?", At]["search_keywords?", MultiVar(str)])
record_pool = on_alconna(quote_alc, priority=2, block=True)

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


@record_pool.handle()
async def record_pool_handle(bot: Bot, event: Event, arp: Arparma, state: T_State):
    """语录查询处理函数"""
    session_id = event.get_session_id()

    if "group" in session_id:
        group_id = session_id.split("_")[1]
        target = PlatformUtils.get_target(group_id=group_id)

        at_user_info: At | None = arp.all_matched_args.get("target_user")
        search_keywords: list[str] = arp.all_matched_args.get("search_keywords", [])

        search_key_processed = ""
        if search_keywords:
            search_key_processed = " ".join(search_keywords)
            logger.debug(f"从MultiVar获取到的关键词列表: {search_keywords}", "群聊语录")

        logger.debug(f"处理后的搜索关键词: '{search_key_processed}'", "群聊语录")

        quoted_user_id_filter: str | None = None
        if at_user_info:
            quoted_user_id_filter = str(at_user_info.target)
            logger.info(f"语录查询指定用户: {quoted_user_id_filter}", "群聊语录")

        final_message_to_send: UniMessage | str | Path | None = None

        if quoted_user_id_filter:
            if search_key_processed:
                quote = await QuoteService.search_quote(
                    group_id, search_key_processed, user_id_filter=quoted_user_id_filter
                )
                if not quote:
                    logger.info(
                        f"用户 {quoted_user_id_filter} 的关键词 '{search_key_processed}' 未找到，尝试随机其语录",
                        "群聊语录",
                    )
                    quote = await QuoteService.get_random_quote(
                        group_id, user_id_filter=quoted_user_id_filter
                    )
                    if quote:
                        msg_content = [
                            At(target=quoted_user_id_filter, flag="user"),
                            f" 关于 '{search_key_processed}' 的语录没找到哦，这是TA的一条随机语录：\n",
                            Path(quote.image_path),
                        ]
                        final_message_to_send = MessageUtils.build_message(msg_content)
                    else:
                        final_message_to_send = MessageUtils.build_message(
                            [
                                At(target=quoted_user_id_filter, flag="user"),
                                " 没有任何语录哦~",
                            ]
                        )
                else:
                    if safe_file_exists(quote.image_path):
                        final_message_to_send = Path(quote.image_path)
                    else:
                        logger.error(
                            f"搜索到的图片不存在: {quote.image_path}", "群聊语录"
                        )
                        quote_id = quote.id
                        await quote.delete()
                        logger.info(
                            f"删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                        )
                        new_quote = await QuoteService.get_random_quote(
                            group_id, user_id_filter=quoted_user_id_filter
                        )
                        if new_quote and safe_file_exists(new_quote.image_path):
                            final_message_to_send = MessageUtils.build_message(
                                [
                                    At(target=quoted_user_id_filter, flag="user"),
                                    f" 关于 '{search_key_processed}' 的语录图片不存在，这是TA的一条随机语录：\n",
                                    Path(new_quote.image_path),
                                ]
                            )
                        else:
                            final_message_to_send = MessageUtils.build_message(
                                [
                                    At(target=quoted_user_id_filter, flag="user"),
                                    " 关于该关键词的语录图片不存在，且无其他可用语录。",
                                ]
                            )
            else:
                quote = await QuoteService.get_random_quote(
                    group_id, user_id_filter=quoted_user_id_filter
                )
                if quote:
                    if safe_file_exists(quote.image_path):
                        final_message_to_send = Path(quote.image_path)
                    else:
                        logger.error(
                            f"随机获取的图片不存在: {quote.image_path}", "群聊语录"
                        )
                        quote_id = quote.id
                        await quote.delete()
                        logger.info(
                            f"删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                        )
                        new_quote = await QuoteService.get_random_quote(
                            group_id, user_id_filter=quoted_user_id_filter
                        )
                        if new_quote and safe_file_exists(new_quote.image_path):
                            final_message_to_send = Path(new_quote.image_path)
                        else:
                            final_message_to_send = MessageUtils.build_message(
                                [
                                    At(target=quoted_user_id_filter, flag="user"),
                                    " 语录图片不存在，且无其他可用语录。",
                                ]
                            )
                else:
                    final_message_to_send = MessageUtils.build_message(
                        [
                            At(target=quoted_user_id_filter, flag="user"),
                            " 没有任何语录哦~",
                        ]
                    )
        else:
            if not search_key_processed:
                quote = await QuoteService.get_random_quote(group_id)
                if not quote:
                    final_message_to_send = "当前无语录库"
                else:
                    if safe_file_exists(quote.image_path):
                        final_message_to_send = Path(quote.image_path)
                    else:
                        logger.error(
                            f"随机获取的图片不存在: {quote.image_path}", "群聊语录"
                        )
                        quote_id = quote.id
                        await quote.delete()
                        logger.info(
                            f"删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                        )
                        new_quote = await QuoteService.get_random_quote(group_id)
                        if new_quote and safe_file_exists(new_quote.image_path):
                            final_message_to_send = Path(new_quote.image_path)
                        else:
                            final_message_to_send = "语录图片不存在，请联系管理员"
            else:
                quote = await QuoteService.search_quote(group_id, search_key_processed)
                if not quote:
                    random_quote_obj = await QuoteService.get_random_quote(group_id)
                    if not random_quote_obj:
                        final_message_to_send = "当前无语录库"
                    else:
                        if safe_file_exists(random_quote_obj.image_path):
                            final_message_to_send = MessageUtils.build_message(
                                [
                                    "当前查询无结果, 为您随机发送。",
                                    Path(random_quote_obj.image_path),
                                ]
                            )
                        else:
                            logger.error(
                                f"随机获取的图片不存在: {random_quote_obj.image_path}",
                                "群聊语录",
                            )
                            quote_id = random_quote_obj.id
                            await random_quote_obj.delete()
                            logger.info(
                                f"删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                            )
                            new_quote = await QuoteService.get_random_quote(group_id)
                            if new_quote and safe_file_exists(new_quote.image_path):
                                final_message_to_send = MessageUtils.build_message(
                                    [
                                        "当前查询无结果, 为您随机发送。",
                                        Path(new_quote.image_path),
                                    ]
                                )
                            else:
                                final_message_to_send = "语录图片不存在，请联系管理员"
                else:
                    if safe_file_exists(quote.image_path):
                        final_message_to_send = Path(quote.image_path)
                    else:
                        logger.error(
                            f"搜索到的图片不存在: {quote.image_path}", "群聊语录"
                        )
                        quote_id = quote.id
                        await quote.delete()
                        logger.info(
                            f"删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                        )
                        new_quote = await QuoteService.get_random_quote(group_id)
                        if new_quote and safe_file_exists(new_quote.image_path):
                            final_message_to_send = MessageUtils.build_message(
                                [
                                    f"关于 '{search_key_processed}' 的语录图片不存在，为您随机发送：",
                                    Path(new_quote.image_path),
                                ]
                            )
                        else:
                            final_message_to_send = "语录图片不存在，请联系管理员"

        if isinstance(final_message_to_send, str):
            await MessageUtils.build_message(final_message_to_send).send(
                target=target, bot=bot
            )
        elif isinstance(final_message_to_send, Path):
            if final_message_to_send.exists():
                if quote:
                    await QuoteService.increment_view_count(quote.id)
                    logger.debug(f"增加语录ID {quote.id} 的查看次数", "群聊语录")
                await MessageUtils.build_message(final_message_to_send).send(
                    target=target, bot=bot
                )
            else:
                logger.error(f"图片不存在: {final_message_to_send}", "群聊语录")

                if quote:
                    quote_id = quote.id
                    logger.info(
                        f"尝试删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                    )
                    await quote.delete()
                    logger.info(
                        f"成功删除不存在图片的语录记录 ID: {quote_id}", "群聊语录"
                    )

                    if quoted_user_id_filter:
                        new_quote = await QuoteService.get_random_quote(
                            group_id, user_id_filter=quoted_user_id_filter
                        )
                    else:
                        new_quote = await QuoteService.get_random_quote(group_id)

                    if new_quote:
                        if safe_file_exists(new_quote.image_path):
                            await QuoteService.increment_view_count(new_quote.id)
                            logger.info(
                                f"重新获取到语录 ID: {new_quote.id}", "群聊语录"
                            )
                            await MessageUtils.build_message(
                                Path(new_quote.image_path)
                            ).send(target=target, bot=bot)
                        else:
                            await MessageUtils.build_message(
                                "图片文件不存在，请联系管理员"
                            ).send(target=target, bot=bot)
                    else:
                        await MessageUtils.build_message("当前无可用语录").send(
                            target=target, bot=bot
                        )
                else:
                    await MessageUtils.build_message(
                        "图片文件不存在，请联系管理员"
                    ).send(target=target, bot=bot)
        elif isinstance(final_message_to_send, UniMessage):
            await final_message_to_send.send(target=target, bot=bot)
        elif final_message_to_send is None:
            logger.warning(
                "final_message_to_send is None, no message to send.", "群聊语录"
            )
        else:
            logger.error(
                f"未知的 final_message_to_send 类型: {type(final_message_to_send)}",
                "群聊语录",
            )
            await MessageUtils.build_message("发生未知错误，无法发送语录。").send(
                target=target, bot=bot
            )


@alltag_cmd.handle()
async def alltag_handle(bot: Bot, event: MessageEvent, arp: Arparma, state: T_State):
    """查看语录标签处理函数"""
    session_id = event.get_session_id()
    user_id = str(event.get_user_id())

    if "group" not in session_id:
        return

    group_id = session_id.split("_")[1]
    errMsg = "请回复需要指定语录"

    try:
        image_basename = await extract_image_basename_from_reply(bot, event)

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

        await bot.send_msg(
            group_id=int(group_id), message=MessageSegment.at(user_id) + msg_text
        )
    except ReplyImageNotFoundException:
        target = PlatformUtils.get_target(group_id=group_id)
        at_msg = MessageUtils.build_message([At(target=user_id, flag="user"), errMsg])
        await at_msg.send(target=target, bot=bot)
        await alltag_cmd.finish()


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
        group_id_to_query = get_group_id_from_session(session_id)

    if not group_id_to_query:
        await quote_stats_cmd.finish(
            "请在群聊中执行此命令，或超级用户使用 -g 指定群号。"
        )
        return

    reply_target = PlatformUtils.get_target(
        group_id=get_group_id_from_session(session_id)
        if "group" in session_id
        else None,
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
