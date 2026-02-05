import os
from typing import Optional, Literal, Union
from nonebot.permission import SUPERUSER
from nonebot.rule import Rule
from arclet.alconna import Alconna, Args, Arparma, MultiVar, Option, Subcommand
from nonebot.adapters.onebot.v11 import (
    Bot,
    Event,
    Bot as V11Bot,
    MessageEvent,
)
from nonebot_plugin_alconna import At, on_alconna
from nonebot_plugin_alconna.uniseg import Image
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo
from nonebot_plugin_waiter import waiter

from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils
from zhenxun.utils.rules import admin_check

from ..config import resolve_quote_image_path
from ..services.quote_service import QuoteService
from ..config import QUOTE_ASSETS_PATH


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


async def uploader_or_admin_check(
    bot: Bot, event: MessageEvent, session: Uninfo
) -> bool:
    """
    检查执行删除操作的用户是否为语录上传者，或者是满足配置权限的管理员。
    """
    if await admin_check("quote", "DELETE_ADMIN_LEVEL")(bot, event, session):
        return True

    if session.group:
        group_id = session.group.id
        user_id = session.user.id
        if image_seg := await _get_image_from_reply(event, bot):
            if image_seg.id:
                image_basename = os.path.basename(image_seg.id)
                quote = await QuoteService.find_quote_by_basename(
                    group_id, image_basename
                )
                if quote and quote.uploader_user_id == user_id:
                    return True
    return False


async def is_reply_to_bot(event: Event) -> bool:
    """检查消息是否为对机器人自身消息的回复"""
    if not isinstance(event, MessageEvent):
        return False

    reply = getattr(event, "reply", None)
    sender = getattr(reply, "sender", None) if reply else None
    sender_id = getattr(sender, "user_id", None) if sender else None

    return sender_id is not None and str(sender_id) == str(
        getattr(event, "self_id", "")
    )


delete_quote_cmd = on_alconna(
    Alconna("删除"),
    aliases={"del"},
    priority=11,
    block=True,
    rule=Rule(is_reply_to_bot),
)


@delete_quote_cmd.handle()
async def handle_delete_quote_standalone(
    bot: Bot, event: MessageEvent, session: Uninfo
):
    """独立的删除语录处理函数"""
    if not await uploader_or_admin_check(bot, event, session):
        await delete_quote_cmd.finish()

    if not session.group:
        logger.debug("删除命令在非群聊环境中使用，已忽略。", "群聊语录")
        return

    group_id = session.group.id
    user_id = session.user.id

    if not (image_seg := await _get_image_from_reply(event, bot)):
        logger.debug("回复的消息中未找到图片，无法执行删除操作。", "群聊语录")
        return

    if not image_seg.id:
        logger.warning("无法获取到回复图片的唯一标识，删除失败。", "群聊语录")
        return

    image_basename = os.path.basename(image_seg.id)

    is_deleted = await QuoteService.delete_quote(group_id, image_basename)

    if is_deleted:
        await MessageUtils.build_message(
            [At(target=user_id, flag="user"), "删除成功"]
        ).send()
    else:
        logger.info(
            f"尝试删除语录失败，图片 '{image_basename}' 不在群组 {group_id} 的语录库中。",
            "群聊语录",
        )


quote_manage_cmd = on_alconna(
    Alconna(
        "quote",
        Subcommand(
            "manager",
            Subcommand(
                "keyword",
                Args["keywords", MultiVar(str)],
                Option("--uploader", Args["user_id", Union[At, int]]),
                Option("--quoted", Args["user_id", Union[At, int]]),
                alias={"删除关键词"},
            ),
            Subcommand(
                "clear",
                Option("--uploader", Args["user_id", Union[At, int]]),
                Option("--quoted", Args["user_id", Union[At, int]]),
                Option("--group", Args["group_id", str]),
                alias={"清空全部"},
            ),
            Subcommand(
                "cleanup",
                Args["target?", Literal["退群用户"], "退群用户"],
                alias={"清理"},
            ),
        ),
        Subcommand("theme", Args["theme_name?", str]),
    ),
    permission=SUPERUSER,
    block=True,
    priority=10,
)

quote_manage_cmd.shortcut("语录管理", {"args": ["manager"]})
quote_manage_cmd.shortcut("语录主题", {"args": ["theme"]})


@quote_manage_cmd.handle()
async def _(bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo):
    if arp.find("manager"):
        await handle_adv_delete(bot, event, arp, session)
    elif arp.find("theme"):
        await handle_theme(bot, event, arp, session)


def get_available_themes() -> list[str]:
    """获取所有可用的语录主题，并确保排序。"""
    available_themes_set = set()
    components_root = QUOTE_ASSETS_PATH / "components"
    if components_root.exists() and components_root.is_dir():
        for component_dir in components_root.iterdir():
            if component_dir.is_dir():
                available_themes_set.add(component_dir.name)

                skins_dir = component_dir / "skins"
                if skins_dir.exists() and skins_dir.is_dir():
                    for skin_dir in skins_dir.iterdir():
                        if skin_dir.is_dir():
                            available_themes_set.add(skin_dir.name)

    return sorted(list(available_themes_set))


async def handle_theme(bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo):
    """处理 'quote theme' 命令"""
    theme_name_or_index: str | None = arp.query("theme.theme_name")

    available_themes = get_available_themes()

    if not theme_name_or_index:
        message_parts = [
            "可用的语录主题列表 (使用 `quote theme [主题名/序号]` 切换):"
        ] + [f"{i + 1}. {th}" for i, th in enumerate(available_themes)]
        await quote_manage_cmd.finish("\n".join(message_parts))
        return

    theme_name = theme_name_or_index
    if theme_name and theme_name.isdigit():
        try:
            theme_index = int(theme_name)
            if 1 <= theme_index <= len(available_themes):
                theme_name = available_themes[theme_index - 1]
        except (ValueError, IndexError):
            pass

    if theme_name in available_themes:
        Config.set_config("quote", "THEME", theme_name, auto_save=True)
        await quote_manage_cmd.finish(f"语录主题已切换为: {theme_name}")
    else:
        await quote_manage_cmd.finish(
            f"主题 '{theme_name_or_index}' 不存在。可用主题有: {', '.join(sorted(available_themes))}"
        )


async def handle_adv_delete(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    """高级删除命令处理函数"""
    current_user_id = str(event.get_user_id())

    if not session.group:
        await quote_manage_cmd.finish("高级管理命令必须在群聊中使用。")
        return
    group_id = session.group.id

    target_for_reply = PlatformUtils.get_target(
        group_id=group_id, user_id=current_user_id
    )
    if not target_for_reply:
        target_for_reply = PlatformUtils.get_target(user_id=current_user_id)

    matched_quotes = []
    confirm_msg_prefix = ""
    user_filter_id: str | None = None

    if arp.find("manager.keyword"):
        keywords: list[str] = arp.query("manager.keyword.keywords", [])
        if not keywords:
            await quote_manage_cmd.finish("请提供至少一个要删除的关键词")

        uploader_param: Union[At, int, None] = arp.query(
            "manager.keyword.uploader.user_id"
        )
        quoted_param: Union[At, int, None] = arp.query("manager.keyword.quoted.user_id")

        user_filter_id = None
        user_filter_type = None

        if uploader_param:
            user_filter_id = (
                str(uploader_param.target)
                if isinstance(uploader_param, At)
                else str(uploader_param)
            )
            user_filter_type = "uploader_user_id"
        elif quoted_param:
            user_filter_id = (
                str(quoted_param.target)
                if isinstance(quoted_param, At)
                else str(quoted_param)
            )
            user_filter_type = "quoted_user_id"

        user_filter_kwargs = (
            {user_filter_type: user_filter_id} if user_filter_type else {}
        )

        matched_quotes = await QuoteService.search_quotes_for_deletion(
            group_id, keywords, **user_filter_kwargs
        )
        confirm_msg_prefix = f"与关键词 '{' 或 '.join(keywords)}' 相关的"

    elif arp.find("manager.clear"):
        uploader_param: Union[At, int, None] = arp.query(
            "manager.clear.uploader.user_id"
        )
        quoted_param: Union[At, int, None] = arp.query("manager.clear.quoted.user_id")
        group_to_clear: str | None = arp.query("manager.clear.group.group_id")

        if uploader_param:
            user_filter_id = (
                str(uploader_param.target)
                if isinstance(uploader_param, At)
                else str(uploader_param)
            )
            matched_quotes = await QuoteService.search_quotes_for_deletion(
                group_id, keywords=None, uploader_user_id=user_filter_id
            )
            confirm_msg_prefix = f"由用户 {user_filter_id} 上传的全部"
        elif quoted_param:
            user_filter_id = (
                str(quoted_param.target)
                if isinstance(quoted_param, At)
                else str(quoted_param)
            )
            matched_quotes = await QuoteService.search_quotes_for_deletion(
                group_id, keywords=None, quoted_user_id=user_filter_id
            )
            confirm_msg_prefix = f"关于用户 {user_filter_id} 的全部"
        elif group_to_clear:
            matched_quotes = await QuoteService.search_quotes_for_deletion(
                group_to_clear
            )
            confirm_msg_prefix = f"群组 {group_to_clear} 的全部"
        else:
            await quote_manage_cmd.finish(
                "使用 '清空全部' 子命令时，必须提供 --uploader, --quoted, 或 --group 中的一个选项。"
            )

    elif arp.find("manager.cleanup"):
        matched_quotes = await QuoteService.find_quotes_from_left_users(group_id, bot)
        confirm_msg_prefix = "由已退群用户产生或记录的"

    if not matched_quotes:
        await MessageUtils.build_message(f"未找到{confirm_msg_prefix}语录").send(
            target=target_for_reply, bot=bot
        )
        return

    count = len(matched_quotes)
    confirm_msg_text = (
        f"在群 {group_id} 中找到 {count} 条{confirm_msg_prefix}语录，"
        f"确认删除请回复'是'，取消请回复其他内容"
    )

    await MessageUtils.build_message(confirm_msg_text).send(
        target=target_for_reply, bot=bot
    )

    @waiter(waits=["message"], keep_session=True)
    async def check_confirm(event: MessageEvent):
        if (
            event.get_session_id() != event.get_session_id()
            or str(event.get_user_id()) != current_user_id
        ):
            return None
        return event.get_plaintext().strip()

    try:
        reply_text = await check_confirm.wait(timeout=30)

        if reply_text is None or reply_text != "是":
            await MessageUtils.build_message("操作已取消").send(
                target=target_for_reply, bot=bot
            )
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
                logger.error(
                    f"删除语录失败 - ID: {quote.id}, 路径: {quote.image_path}, 错误: {e}",
                    "群聊语录",
                    e=e,
                )
                failed_count += 1

        result_msg = f"语录删除完成，成功: {deleted_count}，失败: {failed_count}"
        await MessageUtils.build_message(result_msg).send(
            target=target_for_reply, bot=bot
        )
    except Exception as e:
        logger.error(f"删除语录过程中发生错误: {e}", "群聊语录", e=e)
        await MessageUtils.build_message(f"删除过程中发生错误: {e}").send(
            target=target_for_reply, bot=bot
        )
