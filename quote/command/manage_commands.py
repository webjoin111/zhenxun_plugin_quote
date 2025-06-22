import os

from arclet.alconna import Alconna, Args, Arparma, MultiVar, Option
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_waiter import waiter

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.rules import admin_check

from ..services.quote_service import QuoteService
from ..utils.message_utils import reply_handle

delete_quote_alc = Alconna("删除")
delete_record = on_alconna(
    delete_quote_alc, aliases={"delete"}, permission=SUPERUSER, block=True
)

addtag_alc = Alconna("addtag", Args["tags", MultiVar(str)])
addtag_cmd = on_alconna(addtag_alc, rule=admin_check(5), block=True)

deltag_alc = Alconna("deltag", Args["tags", MultiVar(str)])
deltag_cmd = on_alconna(deltag_alc, rule=admin_check(5), block=True)

delete_by_keyword_alc = Alconna(
    "删除关键词",
    Args["target_user?", At]["keywords?", MultiVar(str)],
    Option("-g", Args["group_id", str], help_text="指定群组ID"),
)
delete_by_keyword_cmd = on_alconna(
    delete_by_keyword_alc, permission=SUPERUSER, block=True
)


@delete_record.handle()
async def delete_record_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, state: T_State
):
    """删除语录处理函数"""
    session_id = event.get_session_id()
    user_id = str(event.get_user_id())

    if "group" not in session_id:
        return

    group_id = session_id.split("_")[1]

    errMsg = "请回复需要删除的语录, 并输入删除指令"
    image_basename = await reply_handle(
        bot, errMsg, event, group_id, user_id, delete_record
    )

    is_deleted = await QuoteService.delete_quote(group_id, image_basename)

    msg_text = "删除成功" if is_deleted else "该图不在语录库中"

    await bot.send_msg(
        group_id=int(group_id), message=MessageSegment.at(user_id) + msg_text
    )


@addtag_cmd.handle()
async def addtag_handle(bot: Bot, event: MessageEvent, arp: Arparma, state: T_State):
    """添加语录标签处理函数"""
    tags_list: list[str] = arp.all_matched_args.get("tags", [])
    if not tags_list:
        await addtag_cmd.finish("请输入至少一个标签。")

    session_id = event.get_session_id()
    user_id = str(event.get_user_id())

    if "group" not in session_id:
        return

    group_id = session_id.split("_")[1]
    errMsg = "请回复需要指定语录"
    image_basename = await reply_handle(
        bot, errMsg, event, group_id, user_id, addtag_cmd
    )

    quote = await QuoteService.find_quote_by_basename(group_id, image_basename)

    msg_text = ""
    if not quote:
        msg_text = "该语录不存在"
    else:
        success = await QuoteService.add_tags(quote, tags_list)
        if success:
            msg_text = f"已为该语录添加上 {' '.join(tags_list)} 标签"
        else:
            msg_text = "添加标签失败，数据库错误"

    await bot.send_msg(
        group_id=int(group_id), message=MessageSegment.at(user_id) + msg_text
    )


@deltag_cmd.handle()
async def deltag_handle(bot: Bot, event: MessageEvent, arp: Arparma, state: T_State):
    """删除语录标签处理函数"""
    tags_list: list[str] = arp.all_matched_args.get("tags", [])
    if not tags_list:
        await deltag_cmd.finish("请输入至少一个要删除的标签。")

    session_id = event.get_session_id()
    user_id = str(event.get_user_id())

    if "group" not in session_id:
        return

    group_id = session_id.split("_")[1]
    errMsg = "请回复需要指定语录"
    image_basename = await reply_handle(
        bot, errMsg, event, group_id, user_id, deltag_cmd
    )

    quote = await QuoteService.find_quote_by_basename(group_id, image_basename)

    msg_text = ""
    if not quote:
        msg_text = "该语录不存在"
    else:
        success = await QuoteService.delete_tags(quote, tags_list)
        if success:
            msg_text = f"已移除该语录的 {' '.join(tags_list)} 标签"
        else:
            msg_text = "删除标签失败，数据库错误"

    await bot.send_msg(
        group_id=int(group_id), message=MessageSegment.at(user_id) + msg_text
    )


@delete_by_keyword_cmd.handle()
async def delete_by_keyword_handle(bot: Bot, event: MessageEvent, arp: Arparma):
    """根据关键词删除语录处理函数"""
    keywords: list[str] = arp.all_matched_args.get("keywords", [])

    keyword = ""
    if keywords:
        keyword = " ".join(keywords)
        logger.debug(f"从MultiVar获取到的关键词列表: {keywords}", "群聊语录")

    logger.debug(f"处理后的关键词: '{keyword}'", "群聊语录")

    if not keyword:
        await delete_by_keyword_cmd.finish("请提供要删除的关键词")
        return

    session_id = event.get_session_id()
    current_user_id = str(event.get_user_id())

    at_user_info: At | None = arp.all_matched_args.get("target_user")
    quoted_user_id_to_delete: str | None = None
    if at_user_info:
        quoted_user_id_to_delete = str(at_user_info.target)

    target_group_id_opt = arp.query("options.g.group_id", None)
    group_id_for_search = ""
    if target_group_id_opt:
        if not target_group_id_opt.isdigit():
            await delete_by_keyword_cmd.finish("群组ID必须是数字")
            return
        group_id_for_search = target_group_id_opt
        logger.info(f"使用指定的群组ID: {group_id_for_search} 进行删除操作", "群聊语录")
    else:
        if "group" not in session_id:
            await delete_by_keyword_cmd.finish(
                "在私聊中使用此命令时，必须使用 -g 参数指定群组ID"
            )
            return
        group_id_for_search = session_id.split("_")[1]

    target_for_reply = PlatformUtils.get_target(
        group_id=session_id.split("_")[1] if "group" in session_id else None,
        user_id=current_user_id if "private" in session_id else None,
    )
    if not target_for_reply:
        target_for_reply = PlatformUtils.get_target(user_id=current_user_id)

    logger.info(
        f"开始搜索关键词 '{keyword}' (用户: {quoted_user_id_to_delete or '任意'}) 相关的语录用于删除，在群组 {group_id_for_search}",
        "群聊语录",
    )

    matched_quotes = await QuoteService.search_quotes_for_deletion(
        group_id_for_search, keyword, user_id_filter=quoted_user_id_to_delete
    )

    if not matched_quotes:
        user_spec = (
            f"用户 {quoted_user_id_to_delete} 的" if quoted_user_id_to_delete else ""
        )
        message_text = f"未找到{user_spec}与关键词 '{keyword}' 相关的语录"
        await MessageUtils.build_message(message_text).send(
            target=target_for_reply, bot=bot
        )
        return

    count = len(matched_quotes)
    is_remote = (
        "group" in session_id and session_id.split("_")[1] != group_id_for_search
    )
    user_confirm_spec = (
        f"用户 {quoted_user_id_to_delete} 的" if quoted_user_id_to_delete else ""
    )

    confirm_msg_text = (
        f"{'[远程操作] ' if is_remote else ''}在群 {group_id_for_search} 中找到 {count} 条{user_confirm_spec}与关键词 '{keyword}' 相关的语录，"
        f"确认删除请回复'是'，取消请回复其他内容"
    )

    msg_content = []
    if quoted_user_id_to_delete:
        msg_content.append(At(target=quoted_user_id_to_delete, flag="user"))
    msg_content.append(confirm_msg_text)

    await MessageUtils.build_message(msg_content).send(target=target_for_reply, bot=bot)

    @waiter(waits=["message"], keep_session=True)
    async def check_confirm(event: MessageEvent):
        if (
            event.get_session_id() != session_id
            or str(event.get_user_id()) != current_user_id
        ):
            return None
        return event.get_plaintext().strip()

    try:
        reply_text = await check_confirm.wait(timeout=30)

        if reply_text is None:
            await MessageUtils.build_message("确认超时，已取消删除操作").send(
                target=target_for_reply, bot=bot
            )
            return

        if reply_text != "是":
            await MessageUtils.build_message("已取消删除操作").send(
                target=target_for_reply, bot=bot
            )
            return

        deleted_count = 0
        failed_count = 0

        for quote in matched_quotes:
            try:
                image_path = quote.image_path
                if os.path.exists(image_path):
                    os.remove(image_path)

                await quote.delete()
                deleted_count += 1

            except Exception as e:
                logger.error(
                    f"删除语录失败 - ID: {quote.id}, 路径: {quote.image_path}, 错误: {e}",
                    "群聊语录",
                    e=e,
                )
                failed_count += 1

        result_msg = f"{'[远程操作] ' if is_remote else ''}群 {group_id_for_search} 中{user_confirm_spec}的语录删除完成，成功: {deleted_count}，失败: {failed_count}"
        await MessageUtils.build_message(result_msg).send(
            target=target_for_reply, bot=bot
        )
    except Exception as e:
        logger.error(f"删除语录过程中发生错误: {e}", "群聊语录", e=e)
        await MessageUtils.build_message(f"删除过程中发生错误: {e}").send(
            target=target_for_reply, bot=bot
        )
