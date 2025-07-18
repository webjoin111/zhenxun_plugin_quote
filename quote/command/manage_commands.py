import os
from typing import Optional, Literal, Union

from arclet.alconna import Alconna, Args, Arparma, MultiVar, Option, Subcommand
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    Bot as V11Bot,
    MessageEvent,
)
from nonebot.permission import SUPERUSER
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_alconna.uniseg import Image
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo
from nonebot_plugin_waiter import waiter

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.rules import admin_check

from ..config import resolve_quote_image_path
from ..services.quote_service import QuoteService


async def _get_image_from_reply(event: Event, bot: Bot) -> Optional[Image]:
    """
    从回复消息中提取图片。
    此函数总是通过API获取消息详情，并直接解析返回的原始数据。
    """
    if not (reply := await reply_fetch(event, bot)):
        return None

    if not isinstance(bot, V11Bot):
        logger.warning(
            f"当前 Bot 类型 ({type(bot)}) 不支持通过API获取消息，无法从回复中提取图片。"
        )
        return None

    try:
        msg_info = await bot.get_msg(message_id=int(reply.id))
        raw_message_data = msg_info.get("message")
    except Exception as e:
        logger.error(
            f"通过 API get_msg(id={reply.id}) 获取回复消息失败: {e}", "群聊语录", e=e
        )
        return None

    if not raw_message_data:
        logger.debug(f"通过 API get_msg(id={reply.id}) 获取到的消息内容为空。")
        return None

    segment_dicts = []
    if isinstance(raw_message_data, dict):
        segment_dicts.append(raw_message_data)
    elif isinstance(raw_message_data, list):
        segment_dicts = raw_message_data
    else:
        logger.debug(f"获取到的消息内容为字符串，不含图片: {raw_message_data}")
        return None

    for seg_dict in segment_dicts:
        if isinstance(seg_dict, dict) and seg_dict.get("type") == "image":
            data = seg_dict.get("data", {})
            return Image(id=data.get("file"), url=data.get("url"))

    return None


delete_quote_alc = Alconna("删除")
delete_record = on_alconna(
    delete_quote_alc,
    aliases={"delete"},
    rule=admin_check("quote", "DELETE_ADMIN_LEVEL"),
    block=True,
    priority=10
)

addtag_alc = Alconna("addtag", Args["tags", MultiVar(str)])
addtag_cmd = on_alconna(addtag_alc, rule=admin_check(2), block=True)

deltag_alc = Alconna("deltag", Args["tags", MultiVar(str)])
deltag_cmd = on_alconna(deltag_alc, rule=admin_check(2), block=True)

adv_delete_alc = Alconna(
    "语录管理",
    Subcommand("删除关键词",
        Args["keywords", MultiVar(str)],
        Option("--uploader", Args["user_id", Union[At, int]]),
        Option("--quoted", Args["user_id", Union[At, int]]),
    ),
    Subcommand("清空全部",
        Option("--uploader", Args["user_id", Union[At, int]]),
        Option("--quoted", Args["user_id", Union[At, int]]),
        Option("--group", Args["group_id", str]),
    ),
    Subcommand("清理",
        Args["target?", Literal["退群用户"], "退群用户"]
    ),
    Option("-g|--in-group", Args["target_group_id", str])
)
adv_delete_cmd = on_alconna(adv_delete_alc, permission=SUPERUSER, block=True, priority=5)


@delete_record.handle()
async def delete_record_handle(
    bot: Bot, event: MessageEvent, session: Uninfo
):
    """删除语录处理函数"""
    if not session.group:
        return

    user_id = session.user.id
    group_id = session.group.id

    if not (image_seg := await _get_image_from_reply(event, bot)):
        await delete_record.finish("请回复需要删除的语录图片。")
        return

    if not image_seg.id:
        await delete_record.finish("无法获取到回复图片的唯一标识，删除失败。")
        return

    image_basename = os.path.basename(image_seg.id)

    is_deleted = await QuoteService.delete_quote(group_id, image_basename)
    msg_text = "删除成功" if is_deleted else "该图不在语录库中"

    await MessageUtils.build_message([At(target=user_id, flag="user"), msg_text]).send()


@addtag_cmd.handle()
async def addtag_handle(bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo):
    """添加语录标签处理函数"""
    tags_list: list[str] = arp.all_matched_args.get("tags", [])
    if not tags_list:
        await addtag_cmd.finish("请输入至少一个标签。")

    if not session.group:
        return

    user_id = session.user.id
    group_id = session.group.id

    if not (image_seg := await _get_image_from_reply(event, bot)):
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
        success = await QuoteService.add_tags(quote, tags_list)
        if success:
            msg_text = f"已为该语录添加上 {' '.join(tags_list)} 标签"
        else:
            msg_text = "添加标签失败，数据库错误"

    await MessageUtils.build_message(
        [At(target=user_id, flag="user"), " " + msg_text]
    ).send()


@deltag_cmd.handle()
async def deltag_handle(bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo):
    """删除语录标签处理函数"""
    tags_list: list[str] = arp.all_matched_args.get("tags", [])
    if not tags_list:
        await deltag_cmd.finish("请输入至少一个要删除的标签。")

    if not session.group:
        return

    user_id = session.user.id
    group_id = session.group.id

    if not (image_seg := await _get_image_from_reply(event, bot)):
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
        success = await QuoteService.delete_tags(quote, tags_list)
        if success:
            msg_text = f"已移除该语录的 {' '.join(tags_list)} 标签"
        else:
            msg_text = "删除标签失败，数据库错误"

    await MessageUtils.build_message(
        [At(target=user_id, flag="user"), " " + msg_text]
    ).send()


@adv_delete_cmd.handle()
async def adv_delete_handle(bot: Bot, event: MessageEvent, arp: Arparma):
    """高级删除命令处理函数"""
    session_id = event.get_session_id()
    current_user_id = str(event.get_user_id())

    target_group_id_opt = arp.query("in-group.target_group_id")
    group_id_for_search = ""
    if target_group_id_opt:
        if not target_group_id_opt.isdigit():
            await adv_delete_cmd.finish("指定的群组ID必须是数字")
        group_id_for_search = target_group_id_opt
    else:
        if "group" not in session_id:
            await adv_delete_cmd.finish("在私聊中使用此命令时，必须使用 -g 参数指定群组ID")
        group_id_for_search = session_id.split("_")[1]

    target_for_reply = PlatformUtils.get_target(
        group_id=session_id.split("_")[1] if "group" in session_id else None,
        user_id=current_user_id if "private" in session_id else None,
    )
    if not target_for_reply:
        target_for_reply = PlatformUtils.get_target(user_id=current_user_id)

    matched_quotes = []
    confirm_msg_prefix = ""
    user_filter_id: str | None = None

    if arp.find("删除关键词"):
        keywords: list[str] = arp.query("删除关键词.keywords", [])
        if not keywords:
            await adv_delete_cmd.finish("请提供至少一个要删除的关键词")

        uploader_param: Union[At, int, None] = arp.query("删除关键词.uploader.user_id")
        quoted_param: Union[At, int, None] = arp.query("删除关键词.quoted.user_id")

        user_filter_id = None
        user_filter_type = None

        if uploader_param:
            user_filter_id = str(uploader_param.target) if isinstance(uploader_param, At) else str(uploader_param)
            user_filter_type = "uploader_user_id"
        elif quoted_param:
            user_filter_id = str(quoted_param.target) if isinstance(quoted_param, At) else str(quoted_param)
            user_filter_type = "quoted_user_id"

        user_filter_kwargs = {user_filter_type: user_filter_id} if user_filter_type else {}

        matched_quotes = await QuoteService.search_quotes_for_deletion(
            group_id_for_search, keywords, **user_filter_kwargs
        )
        confirm_msg_prefix = f"与关键词 '{' 或 '.join(keywords)}' 相关的"

    elif arp.find("清空全部"):
        uploader_param: Union[At, int, None] = arp.query("清空全部.uploader.user_id")
        quoted_param: Union[At, int, None] = arp.query("清空全部.quoted.user_id")
        group_to_clear: str | None = arp.query("清空全部.group.group_id")

        if uploader_param:
            user_filter_id = str(uploader_param.target) if isinstance(uploader_param, At) else str(uploader_param)
            matched_quotes = await QuoteService.search_quotes_for_deletion(group_id_for_search, uploader_user_id=user_filter_id)
            confirm_msg_prefix = f"由用户 {user_filter_id} 上传的全部"
        elif quoted_param:
            user_filter_id = str(quoted_param.target) if isinstance(quoted_param, At) else str(quoted_param)
            matched_quotes = await QuoteService.search_quotes_for_deletion(group_id_for_search, quoted_user_id=user_filter_id)
            confirm_msg_prefix = f"关于用户 {user_filter_id} 的全部"
        elif group_to_clear:
            matched_quotes = await QuoteService.search_quotes_for_deletion(group_to_clear)
            confirm_msg_prefix = f"群组 {group_to_clear} 的全部"
        else:
            await adv_delete_cmd.finish("使用 '清空全部' 子命令时，必须提供 --uploader, --quoted, 或 --group 中的一个选项。")

    elif arp.find("清理"):
        matched_quotes = await QuoteService.find_quotes_from_left_users(group_id_for_search, bot)
        confirm_msg_prefix = "由已退群用户产生或记录的"

    if not matched_quotes:
        await MessageUtils.build_message(f"未找到{confirm_msg_prefix}语录").send(target=target_for_reply, bot=bot)
        return

    count = len(matched_quotes)
    confirm_msg_text = (
        f"在群 {group_id_for_search} 中找到 {count} 条{confirm_msg_prefix}语录，"
        f"确认删除请回复'是'，取消请回复其他内容"
    )

    await MessageUtils.build_message(confirm_msg_text).send(target=target_for_reply, bot=bot)

    @waiter(waits=["message"], keep_session=True)
    async def check_confirm(event: MessageEvent):
        if event.get_session_id() != session_id or str(event.get_user_id()) != current_user_id:
            return None
        return event.get_plaintext().strip()

    try:
        reply_text = await check_confirm.wait(timeout=30)

        if reply_text is None or reply_text != "是":
            await MessageUtils.build_message("操作已取消").send(target=target_for_reply, bot=bot)
            return

        deleted_count = 0
        failed_count = 0

        for quote in matched_quotes:
            try:
                absolute_image_path = resolve_quote_image_path(quote.image_path)
                if os.path.exists(absolute_image_path):
                    os.remove(absolute_image_path)
                await quote.delete()
                deleted_count += 1
            except Exception as e:
                logger.error(f"删除语录失败 - ID: {quote.id}, 路径: {quote.image_path}, 错误: {e}", "群聊语录", e=e)
                failed_count += 1

        result_msg = f"语录删除完成，成功: {deleted_count}，失败: {failed_count}"
        await MessageUtils.build_message(result_msg).send(target=target_for_reply, bot=bot)
    except Exception as e:
        logger.error(f"删除语录过程中发生错误: {e}", "群聊语录", e=e)
        await MessageUtils.build_message(f"删除过程中发生错误: {e}").send(target=target_for_reply, bot=bot)
