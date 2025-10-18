import base64
import hashlib
import os
import html
import re
import uuid
from typing import cast

import aiofiles
from arclet.alconna import Alconna, Args, Arparma, Option
import httpx
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.typing import T_State
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna.uniseg import (
    At,
    Image as UniImage,
    Text,
    Reply,
    UniMessage,
    Segment,
)
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from zhenxun import ui
from zhenxun.configs.config import Config
from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

from ..config import ensure_quote_path
from ..command.manage_commands import get_available_themes
from ..model import Quote, QuoteCardData, QuoteSequenceData, QuotedReplyData
from ..services.ocr_service import OCRService
from ..services.quote_service import QuoteService
from ..utils.exceptions import ImageProcessError, NetworkError
from ..utils.image_utils import get_img_hash, get_img_hash_from_bytes

from zhenxun.services import avatar_service


def _is_simple_text_message(uni_message: UniMessage) -> bool:
    """
    判断消息是否为“简单纯文本”（可包含@）。
    """
    if not uni_message:
        return False

    allowed_types: tuple[type[Segment], ...] = (Text, At, Reply)
    has_text = False
    for seg in uni_message:
        if not isinstance(seg, allowed_types):
            return False
        if isinstance(seg, Text) and seg.text.strip():
            has_text = True

    return has_text


async def _extract_info_from_reply(event: MessageEvent, bot: Bot):
    """
    仅从回复消息中提取信息，不执行任何网络或渲染操作。
    返回一个包含信息的元组，或一个包含错误信息的元组。
    """
    reply = await reply_fetch(event, bot)
    if not reply or not reply.msg:
        return None, "请回复需要处理的消息，或无法获取回复的详细消息内容。"

    if not (event.reply and event.reply.sender):
        return None, "无法获取回复者信息。"

    uni_message = await UniMessage.generate(message=reply.msg, bot=bot)  # type: ignore

    is_empty = not any(
        isinstance(seg, (Text, UniImage))
        and (not isinstance(seg, Text) or seg.text.strip())
        for seg in uni_message
    )
    if is_empty:
        return None, "回复的消息内容为空。"

    sender = event.reply.sender
    qqid = str(sender.user_id)
    card = sender.card or sender.nickname or qqid
    logger.debug(f"sender: {sender}")
    return (uni_message, card, qqid), None


async def _process_nested_reply(
    message_array: list[dict], bot: Bot
) -> QuotedReplyData | None:
    """
    检查消息段中是否存在对另一条消息的回复（即"引用中的引用"），
    如果存在，则处理并返回一个 QuotedReplyData 对象。
    """
    try:
        if not isinstance(message_array, list):
            return None

        for seg_dict in message_array:
            if seg_dict.get("type") == "reply":
                grandparent_id = int(seg_dict.get("data", {}).get("id", 0))
                if not grandparent_id:
                    continue

                grandparent_msg_info = await bot.get_msg(message_id=grandparent_id)

                gp_sender = grandparent_msg_info["sender"]
                gp_user_id = str(gp_sender["user_id"])
                gp_author = (
                    gp_sender.get("card") or gp_sender.get("nickname") or gp_user_id
                )

                raw_gp_message_array = grandparent_msg_info["message"]
                gp_message_obj = Message(
                    MessageSegment(d["type"], d["data"]) for d in raw_gp_message_array
                )
                gp_uni_msg = await UniMessage.generate(message=gp_message_obj, bot=bot)

                gp_content_list = []
                for seg in gp_uni_msg:
                    if isinstance(seg, Text) and seg.text.strip():
                        gp_content_list.append({"type": "text", "value": seg.text})
                    elif isinstance(seg, UniImage):
                        try:
                            if seg.path:
                                async with aiofiles.open(seg.path, "rb") as img_f:
                                    img_bytes = await img_f.read()
                            elif seg.url:
                                async with httpx.AsyncClient() as client:
                                    resp = await client.get(seg.url)
                                    resp.raise_for_status()
                                    img_bytes = resp.content
                            else:
                                continue

                            img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                            gp_content_list.append(
                                {
                                    "type": "image",
                                    "value": f"data:image/png;base64,{img_base64}",
                                }
                            )
                        except Exception as e:
                            logger.warning(
                                f"处理嵌套引用内图片失败: {e}", "群聊语录", e=e
                            )

                gp_avatar_path = await avatar_service.get_avatar_path(
                    platform="qq", identifier=gp_user_id
                )
                if gp_avatar_path:
                    async with aiofiles.open(gp_avatar_path, "rb") as f:
                        gp_avatar_bytes = await f.read()
                    gp_avatar_base64 = base64.b64encode(gp_avatar_bytes).decode("utf-8")
                    return QuotedReplyData(
                        avatar_data_url=f"data:image/png;base64,{gp_avatar_base64}",
                        author=gp_author,
                        text=gp_content_list,
                    )
    except Exception as e:
        logger.debug(f"处理'引用中引用'失败(或消息不含嵌套引用): {e}", "群聊语录")
    return None


async def _generate_quote_from_reply(
    event: MessageEvent, bot: Bot, uni_message: UniMessage, variant: str | None = None
):
    """
    从回复消息中提取信息并生成语录图片。
    这是一个辅助函数，用于合并 make_record 和 render_quote 的公共逻辑。
    """
    message_to_render = uni_message

    sender = event.reply.sender
    qqid = str(sender.user_id)

    replied_msg_id = cast(int, event.reply.message_id)
    full_replied_msg_info = await bot.get_msg(message_id=replied_msg_id)
    message_array = full_replied_msg_info.get("message", [])
    quoted_reply_data = await _process_nested_reply(message_array, bot)

    group_id = str(event.group_id)

    card, author_role, author_title, author_level_info = None, None, None, None

    try:
        logger.info(
            f"开始调用 get_group_member_info API - Group: {group_id}, User: {qqid}",
            "群聊语录",
        )
        member_info = await bot.call_api(
            "get_group_member_info",
            group_id=int(group_id),
            user_id=int(qqid),
            no_cache=True,
        )
        logger.debug(f"API 响应: {member_info}", "群聊语录")

        if member_info and isinstance(member_info, dict):
            card = member_info.get("card") or member_info.get("nickname") or qqid
            author_role = member_info.get("role")
            author_title = member_info.get("title")
            author_level_info = (
                f"LV{member_info.get('level')}" if member_info.get("level") else None
            )
            logger.info(
                f"成功从 API 获取到详细信息 - 角色: {author_role}, 等级: {author_level_info}, 头衔: {author_title}",
                "群聊语录",
            )
        else:
            raise ValueError("API返回数据格式不正确或为空")

    except Exception as e:
        logger.warning(
            f"调用 get_group_member_info API 失败: {e}。将回退到 event.reply.sender 数据。",
            "群聊语录",
            e=e,
        )
        card = sender.card or sender.nickname or qqid
        author_role = getattr(sender, "role", None)
        author_title = getattr(sender, "title", None)
        author_level_info = (
            f"LV{getattr(sender, 'level', '')}"
            if getattr(sender, "level", None)
            else None
        )

    try:
        avatar_path = await avatar_service.get_avatar_path(
            platform="qq", identifier=qqid
        )
        if not avatar_path:
            raise NetworkError("获取头像失败")

        async with aiofiles.open(avatar_path, "rb") as f:
            avatar_data = await f.read()

        content_list = []
        current_text_parts = []

        async def flush_text():
            nonlocal current_text_parts
            if current_text_parts:
                content_list.append(
                    {"type": "text", "value": "".join(current_text_parts)}
                )
                current_text_parts = []

        for seg in message_to_render:
            if isinstance(seg, Text):
                if seg.text:
                    current_text_parts.append(html.escape(seg.text))
            elif isinstance(seg, At):
                at_qq = seg.target
                at_name = seg.display or at_qq
                try:
                    member_info = await bot.get_group_member_info(
                        group_id=int(group_id), user_id=int(at_qq)
                    )
                    at_name = (
                        member_info.get("card")
                        or member_info.get("nickname")
                        or at_name
                    )
                except Exception:
                    pass
                current_text_parts.append(
                    f'<span class="message-at">@{html.escape(at_name)}</span>'
                )
            elif isinstance(seg, UniImage):
                await flush_text()
                try:
                    if seg.path:
                        async with aiofiles.open(seg.path, "rb") as img_f:
                            img_bytes = await img_f.read()
                    elif seg.url:
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(seg.url)
                            resp.raise_for_status()
                            img_bytes = resp.content
                    else:
                        continue

                    img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                    content_list.append(
                        {
                            "type": "image",
                            "value": f"data:image/png;base64,{img_base64}",
                        }
                    )
                except Exception as e:
                    logger.warning(f"处理语录内图片失败: {e}", "群聊语录", e=e)

        await flush_text()

        img_data = await QuoteService.generate_temp_quote(
            avatar_bytes=avatar_data,
            text=content_list,
            author=card,
            variant=variant or None,
            author_role=author_role,
            author_title=author_title,
            author_level=author_level_info,
            quoted_reply=quoted_reply_data,
        )
        return (img_data, card, qqid, quoted_reply_data), None
    except (NetworkError, ImageProcessError, FileNotFoundError) as e:
        return None, str(e)
    except Exception as e:
        logger.error(f"生成语录图片时发生未知错误: {e}", "群聊语录", e=e)
        return None, f"生成语录图片时发生未知错误: {e}"


MAX_RECORD_COUNT = 10


async def _generate_sequence_from_history(
    bot: Bot,
    group_id: str,
    messages: list[dict],
    variant: str | None,
) -> tuple[bytes, str, str]:
    """从多条历史消息记录生成语录序列图片"""
    card_data_list = []
    recorded_text_parts = []
    last_quoted_user_id = ""

    messages.sort(key=lambda m: m.get("time", 0))

    for msg_info in messages:
        sender = msg_info["sender"]
        qqid = str(sender["user_id"])
        last_quoted_user_id = qqid

        card, author_role, author_title, author_level_info = None, None, None, None
        try:
            member_info = await bot.get_group_member_info(
                group_id=int(group_id), user_id=int(qqid), no_cache=True
            )
            card = member_info.get("card") or member_info.get("nickname") or qqid
            author_role = member_info.get("role")
            author_title = member_info.get("title")
            author_level_info = (
                f"LV{member_info.get('level')}" if member_info.get("level") else None
            )
        except Exception:
            card = sender.get("card") or sender.get("nickname") or qqid

        avatar_path = await avatar_service.get_avatar_path(
            platform="qq", identifier=qqid
        )
        if not avatar_path:
            raise NetworkError(f"获取用户 {qqid} 的头像失败")

        async with aiofiles.open(avatar_path, "rb") as f:
            avatar_data = await f.read()

        raw_message_array = msg_info.get("message", [])
        if isinstance(raw_message_array, str):
            raw_message_array = [{"type": "text", "data": {"text": raw_message_array}}]

        reply_prefix = ""
        quoted_reply_data = await _process_nested_reply(raw_message_array, bot)
        if quoted_reply_data:
            quoted_text_parts = []
            for seg in quoted_reply_data.text:
                if seg.get("type") == "text":
                    quoted_text_parts.append(seg.get("value", ""))
            quoted_text_plain = "".join(quoted_text_parts)
            if quoted_text_plain:
                reply_prefix = (
                    f"「回复 {quoted_reply_data.author}: {quoted_text_plain}」\n"
                )

        message_obj = Message(
            MessageSegment(d["type"], d["data"]) for d in raw_message_array
        )
        uni_message = await UniMessage.generate(message=message_obj, bot=bot)

        content_list = []
        current_text_parts = []
        text_for_record = ""

        async def flush_text_seq():
            nonlocal current_text_parts, text_for_record
            if current_text_parts:
                full_text = "".join(current_text_parts)
                content_list.append({"type": "text", "value": full_text})
                text_for_record += re.sub(r"<[^>]+>", "", full_text)
                current_text_parts = []

        for seg in uni_message:
            if isinstance(seg, Text) and seg.text:
                current_text_parts.append(html.escape(seg.text))
            elif isinstance(seg, At):
                at_qq = seg.target
                at_name = seg.display or at_qq
                try:
                    member_info_at = await bot.get_group_member_info(
                        group_id=int(group_id), user_id=int(at_qq)
                    )
                    at_name = (
                        member_info_at.get("card")
                        or member_info_at.get("nickname")
                        or at_name
                    )
                except Exception:
                    pass
                current_text_parts.append(
                    f'<span class="message-at">@{html.escape(at_name)}</span>'
                )
            elif isinstance(seg, UniImage):
                await flush_text_seq()
                text_for_record += "[图片]"
                try:
                    if seg.path:
                        async with aiofiles.open(seg.path, "rb") as img_f:
                            img_bytes = await img_f.read()
                    elif seg.url:
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(seg.url)
                            resp.raise_for_status()
                            img_bytes = resp.content
                    else:
                        continue
                    img_base64 = base64.b64encode(img_bytes).decode("utf-8")
                    content_list.append(
                        {
                            "type": "image",
                            "value": f"data:image/png;base64,{img_base64}",
                        }
                    )
                except Exception as e:
                    logger.warning(f"处理语录内图片失败: {e}", "群聊语录", e=e)

        await flush_text_seq()
        recorded_text_parts.append(f"{reply_prefix}{card}: {text_for_record}")

        card_data_list.append(
            QuoteCardData(
                avatar_data_url=f"data:image/png;base64,{base64.b64encode(avatar_data).decode('utf-8')}",
                text=content_list,
                author=card,
                author_role=author_role,
                author_title=author_title,
                author_level=author_level_info,
                variant=variant or "default",
                quoted_reply=quoted_reply_data,
            )
        )

    sequence_data = QuoteSequenceData(messages=card_data_list)
    img_data = await ui.render(sequence_data)
    return img_data, "\n".join(recorded_text_parts), last_quoted_user_id


upload_alc = Alconna("上传", Args["image?", UniImage])
save_img_cmd = on_alconna(upload_alc, auto_send_output=False, block=True)
make_record_alc = Alconna(
    "记录",
    Args(),
    Option("-s|--style", Args["style_name", str], help_text="指定主题样式"),
    Option("-n|--num", Args["count", int, 1], help_text="记录连续消息的数量"),
)
make_record_cmd = on_alconna(make_record_alc, block=True)

generate_quote_alc = Alconna(
    "生成",
    Args(),
    Option("-s|--style", Args["style_name", str], help_text="指定主题样式"),
    Option("-n|--num", Args["count", int, 1], help_text="生成连续消息的数量"),
)
generate_quote_cmd = on_alconna(generate_quote_alc, block=True)


@save_img_cmd.handle()
async def save_img_handle(bot: Bot, event: MessageEvent, arp: Arparma, state: T_State):
    """上传语录处理函数"""
    session_id = event.get_session_id()
    message_id = event.message_id
    user_id = str(event.get_user_id())

    file_name = ""
    image_url_for_httpx = None

    image_from_alconna = arp.query("image", None)
    has_image_in_command = False

    if image_from_alconna:
        has_image_in_command = True
        if hasattr(image_from_alconna, "data"):
            file_name = image_from_alconna.data.get("file", "")
            image_url_for_httpx = image_from_alconna.data.get("url", None)
            logger.info(
                f"从 Alconna 解析结果中获取图片: file={file_name}, url={image_url_for_httpx}",
                "群聊语录",
            )

    if not has_image_in_command:
        for seg in event.message:
            if seg.type == "image":
                file_name = seg.data.get("file", "")
                image_url_for_httpx = seg.data.get("url", None)
                has_image_in_command = True
                logger.info(
                    f"在命令中找到图片: file={file_name}, url={image_url_for_httpx}",
                    "群聊语录",
                )
                break

    if not has_image_in_command:
        if event.reply:
            for seg in event.reply.message:
                if seg.type == "image":
                    file_name = seg.data.get("file", "")
                    image_url_for_httpx = seg.data.get("url", None)
                    break
            if not file_name and not image_url_for_httpx:
                await save_img_cmd.finish(
                    "回复的消息中未直接找到图片文件标识或URL，请确认回复的是图片消息。"
                )
            elif not file_name and image_url_for_httpx:
                logger.info(
                    f"未在回复中找到图片file字段, 但找到了URL: {image_url_for_httpx}. 尝试使用httpx下载.",
                    "群聊语录",
                )
        else:
            await save_img_cmd.finish("请直接发送「上传+图片」或回复图片消息来上传语录")

    image_path = ""

    if file_name:
        try:
            resp_image_info = await bot.call_api("get_image", **{"file": file_name})
            image_local_path_gocq = resp_image_info["file"]
            base_img_name = os.path.basename(image_local_path_gocq)
            quote_path = ensure_quote_path()
            image_path = quote_path / base_img_name
            async with aiofiles.open(image_local_path_gocq, "rb") as src:
                content = await src.read()
            async with aiofiles.open(image_path, "wb") as dst:
                await dst.write(content)

        except Exception as e:
            logger.warning(
                f"bot.call_api get_image 失败 (file={file_name})，错误: {e}. "
                f"如果提供了URL ({image_url_for_httpx}), 尝试 httpx 下载.",
                "群聊语录",
                e=e,
            )
            if image_url_for_httpx:
                file_name = ""
            else:
                await save_img_cmd.finish(f"处理图片失败: {e}")
                return

    img_data = None
    temp_image_path = None

    if not image_path and image_url_for_httpx:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(image_url_for_httpx)
                if response.status_code == 200:
                    img_data = response.content
                    quote_path = ensure_quote_path()
                    temp_image_path = quote_path / f"temp_{uuid.uuid4().hex}.png"
                    async with aiofiles.open(temp_image_path, "wb") as f:
                        await f.write(img_data)
                else:
                    await save_img_cmd.finish(
                        f"httpx 下载失败, status: {response.status_code}"
                    )
                    return
        except Exception as httpx_e:
            await save_img_cmd.finish(f"httpx 下载异常: {httpx_e}")
            return
    elif image_path:
        try:
            async with aiofiles.open(image_path, "rb") as f:
                img_data = await f.read()
            temp_image_path = image_path
        except Exception as e:
            await save_img_cmd.finish(f"读取图片失败: {e}")
            return

    if not img_data or not temp_image_path:
        await save_img_cmd.finish("未能成功获取图片数据.")
        return

    image_hash = await get_img_hash(temp_image_path)
    ocr_content = await OCRService.recognize_text(str(temp_image_path))

    if "group" in session_id:
        group_id = session_id.split("_")[1]

        image_name = hashlib.md5(img_data).hexdigest() + ".png"
        quote_path = ensure_quote_path()
        final_image_path = quote_path / image_name

        async with aiofiles.open(final_image_path, "wb") as f:
            await f.write(img_data)

        if temp_image_path != final_image_path and os.path.exists(temp_image_path):
            try:
                os.remove(temp_image_path)
                logger.info(f"删除临时文件: {temp_image_path}", "群聊语录")
            except Exception as e:
                logger.error(f"删除临时文件失败: {e}", "群聊语录", e=e)

        quote, is_new = await QuoteService.add_quote(
            group_id=group_id,
            image_path=str(final_image_path),
            ocr_content=ocr_content,
            recorded_text=None,
            uploader_user_id=user_id,
            image_hash=image_hash,
        )

        if quote:
            if is_new:
                await bot.call_api(
                    "send_group_msg",
                    **{
                        "group_id": int(group_id),
                        "message": MessageSegment.reply(message_id) + "保存成功",
                    },
                )
            else:
                if os.path.exists(final_image_path):
                    os.remove(final_image_path)
                await bot.call_api(
                    "send_group_msg",
                    **{
                        "group_id": int(group_id),
                        "message": MessageSegment.reply(message_id) + "不要重复记录",
                    },
                )
        else:
            await bot.call_api(
                "send_group_msg",
                **{
                    "group_id": int(group_id),
                    "message": (
                        MessageSegment.reply(message_id) + "保存失败，可能是数据库错误"
                    ),
                },
            )
    else:
        logger.info(
            f"上传指令在非群聊环境 ({session_id}) 中被调用，未处理。", "群聊语录"
        )
        await save_img_cmd.send("上传功能目前仅支持群聊。")


async def _handle_quote_generation(
    bot: Bot,
    event: MessageEvent,
    arp: Arparma,
    session: Uninfo,
    issuer_user_id: str | None = None,
) -> tuple[bytes | None, str | None, str | None, str | None]:
    """
    统一处理'记录'和'生成'命令的核心逻辑。

    返回:
        元组 (img_data, recorded_text, quoted_user_id, error_msg)
    """
    user_variant: str | None = arp.query("style.style_name")
    count: int = arp.query("num.count", 1) if not user_variant == "classic" else 1

    if count > MAX_RECORD_COUNT:
        return None, None, None, f"一次最多只能处理 {MAX_RECORD_COUNT} 条消息哦。"

    if count == 1:
        info, error = await _extract_info_from_reply(event, bot)
        if error:
            return None, None, None, error

        assert info is not None
        uni_message, card, qqid = info

        allow_self_record = Config.get_config("quote", "QUOTE_ALLOW_SELF_RECORD", False)
        if issuer_user_id and not allow_self_record and str(qqid) == issuer_user_id:
            return None, None, None, "不允许记录自己的消息，如需开启请联系管理员。"

        replied_msg_id = cast(int, event.reply.message_id)
        full_replied_msg_info = await bot.get_msg(message_id=replied_msg_id)
        message_array = full_replied_msg_info.get("message", [])
        quoted_reply_data = await _process_nested_reply(message_array, bot)
        has_nested_reply = quoted_reply_data is not None

        if user_variant and user_variant.isdigit():
            try:
                theme_index = int(user_variant)
                available_themes = get_available_themes()
                if 1 <= theme_index <= len(available_themes):
                    user_variant = available_themes[theme_index - 1]
                else:
                    return (
                        None,
                        None,
                        None,
                        f"无效的主题序号 '{theme_index}'。请从 1 到 {len(available_themes)} 中选择。",
                    )
            except (ValueError, IndexError):
                pass

        if user_variant:
            final_variant = user_variant
        else:
            final_variant = None
            is_simple_text = _is_simple_text_message(uni_message)

            if is_simple_text and not has_nested_reply:
                text_only_theme = Config.get_config(
                    "quote", "QUOTE_TEXT_ONLY_THEME", ""
                )
                if text_only_theme:
                    final_variant = text_only_theme

            if not final_variant:
                final_variant = Config.get_config("quote", "QUOTE_THEME", "qq-native")

        if final_variant == "classic":
            is_pure_image = not any(
                isinstance(seg, Text) and seg.text.strip() for seg in uni_message
            ) and any(isinstance(seg, UniImage) for seg in uni_message)
            if is_pure_image:
                return None, None, None, "不支持使用 classic 主题记录纯图片消息。"

        recorded_text_content = uni_message.extract_plain_text()
        recorded_text = f"{card} {recorded_text_content}"

        result, error_render = await _generate_quote_from_reply(
            event, bot, uni_message, final_variant
        )
        if error_render:
            return None, None, None, error_render

        assert result is not None
        img_data, _, _, _ = result

        if quoted_reply_data:
            quoted_text_parts = []
            for seg in quoted_reply_data.text:
                if seg.get("type") == "text":
                    quoted_text_parts.append(seg.get("value", ""))
            quoted_text_plain = "".join(quoted_text_parts)
            if quoted_text_plain:
                reply_prefix = (
                    f"「回复 {quoted_reply_data.author}: {quoted_text_plain}」\n"
                )
                recorded_text = f"{reply_prefix}{recorded_text}"

        return img_data, recorded_text, str(qqid), None

    else:
        if not session.group:
            return None, None, None, "连续消息处理功能仅限群聊使用。"

        reply = await reply_fetch(event, bot)
        if not reply or not reply.msg:
            return None, None, None, "请回复需要作为结尾的那条消息。"

        if user_variant == "classic":
            return None, None, None, "不支持使用 classic 主题处理连续消息。"

        group_id = session.group.id
        start_msg_id = int(reply.id)
        message_history = []
        try:
            replied_msg_info = await bot.get_msg(message_id=start_msg_id)
            anchor_seq = replied_msg_info.get("message_seq")
            if not anchor_seq:
                return None, None, None, "获取被回复消息的序列号失败，无法处理。"

            history_result = await bot.call_api(
                "get_group_msg_history",
                **{
                    "group_id": int(group_id),
                    "message_seq": anchor_seq,
                    "count": count,
                    "reverseOrder": True,
                },
            )
            raw_messages = history_result.get("messages", [])
            logger.debug(f"raw_messages: {raw_messages}")

            valid_messages = [
                msg
                for msg in raw_messages
                if msg["sender"]["user_id"] != event.self_id
                and _is_message_renderable(msg)
            ]

            valid_messages.sort(key=lambda m: m.get("time", 0))
            message_history = valid_messages
        except Exception as e:
            return None, None, None, f"获取历史消息时出错: {e}"

        if not message_history:
            return None, None, None, "未能获取到任何有效的历史消息。"

        last_message_user_id = str(message_history[-1]["sender"]["user_id"])
        allow_self_record = Config.get_config("quote", "QUOTE_ALLOW_SELF_RECORD", False)
        if (
            issuer_user_id
            and not allow_self_record
            and last_message_user_id == issuer_user_id
        ):
            return None, None, None, "不允许记录自己的消息，如需开启请联系管理员。"

        try:
            (
                img_data,
                recorded_text,
                quoted_user_id,
            ) = await _generate_sequence_from_history(
                bot, group_id, message_history, user_variant
            )
            return img_data, recorded_text, quoted_user_id, None
        except (NetworkError, ImageProcessError, FileNotFoundError) as e:
            return None, None, None, f"生成语录序列失败: {e}"


def _is_message_renderable(message_dict: dict) -> bool:
    """
    检查一条消息是否包含可渲染的内容，并排除不支持的类型。
    - 显式排除合并转发消息。
    - 只允许包含文本或图片的消息通过。
    """
    message_segments = message_dict.get("message", [])
    if not isinstance(message_segments, list):
        return isinstance(message_segments, str) and bool(message_segments.strip())

    if any(seg.get("type") == "forward" for seg in message_segments):
        return False

    return any(seg.get("type") in {"text", "image"} for seg in message_segments)


@make_record_cmd.handle()
async def make_record_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    """记录语录处理函数 (重构后)"""
    user_id = str(event.get_user_id())

    img_data, recorded_text, quoted_user_id, error = await _handle_quote_generation(
        bot, event, arp, session, issuer_user_id=user_id
    )

    if error:
        await make_record_cmd.finish(error)
        return

    assert img_data is not None
    assert recorded_text is not None

    image_hash = await get_img_hash_from_bytes(img_data)
    group_id = session.group.id if session.group else ""

    if await Quote.filter(group_id=group_id, image_hash=image_hash).exists():
        await MessageUtils.build_message("不要重复记录").send(target=event, bot=bot)
        return

    image_name = hashlib.md5(img_data).hexdigest() + ".png"
    image_path = ensure_quote_path() / image_name

    try:
        async with aiofiles.open(image_path, "wb") as file:
            await file.write(img_data)

        quote, is_new = await QuoteService.add_quote(
            group_id=group_id,
            image_path=str(image_path),
            ocr_content=None,
            recorded_text=recorded_text,
            uploader_user_id=user_id,
            quoted_user_id=quoted_user_id,
            image_hash=image_hash,
        )

        if quote and is_new:
            await MessageUtils.build_message(img_data).send(target=event, bot=bot)
        else:
            if os.path.exists(image_path):
                os.remove(image_path)
            msg = "不要重复记录" if not is_new else "保存语录时发生意外，请稍后再试"
            await MessageUtils.build_message(msg).send(target=event, bot=bot)
    except Exception as e:
        logger.error(f"记录语录过程中发生IO或数据库错误: {e}", "群聊语录", e=e)
        if os.path.exists(image_path):
            os.remove(image_path)
        await MessageUtils.build_message("保存语录时发生意外，请稍后再试").send(
            target=event, bot=bot
        )


@generate_quote_cmd.handle()
async def generate_quote_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    """生成语录处理函数 (重构后支持多条消息)"""
    img_data, _, _, error = await _handle_quote_generation(bot, event, arp, session)

    if error:
        await generate_quote_cmd.finish(error)
        return

    if img_data:
        await MessageUtils.build_message(img_data).send(target=event, bot=bot)
    else:
        await generate_quote_cmd.finish("生成语录图片失败。")
