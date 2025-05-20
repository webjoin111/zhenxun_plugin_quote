import json
import os
from typing import Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot_plugin_alconna import At

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils
from zhenxun.utils.platform import PlatformUtils


async def send_group_message(
    bot: Bot,
    group_id: str,
    message: str | MessageSegment | list[MessageSegment] | bytes,
) -> None:
    """发送群消息"""
    target = PlatformUtils.get_target(group_id=group_id)

    if isinstance(message, str) or isinstance(message, bytes):
        uni_message = MessageUtils.build_message(message)
    elif isinstance(message, list):
        segments = []
        for seg in message:
            if seg.type == "text":
                segments.append(seg.data["text"])
            elif seg.type == "image":
                segments.append(seg.data["file"])
            else:
                segments.append(str(seg))
        uni_message = MessageUtils.build_message(segments)
    else:
        if message.type == "text":
            uni_message = MessageUtils.build_message(message.data["text"])
        elif message.type == "image":
            uni_message = MessageUtils.build_message(message.data["file"])
        else:
            uni_message = MessageUtils.build_message(str(message))

    await uni_message.send(target=target, bot=bot)


async def extract_image_from_reply(
    event: MessageEvent,
) -> tuple[str | None, str | None]:
    """提取回复中的图片"""
    file_name = None
    image_url = None

    if event.reply:
        for seg in event.reply.message:
            if seg.type == "image":
                file_name = seg.data.get("file", "")
                image_url = seg.data.get("url", None)
                break

    return file_name, image_url


async def extract_image_from_message_id(bot: Bot, message_id: int) -> str:
    """从消息ID中提取图片文件名"""
    from .exceptions import ReplyImageNotFoundException

    try:
        resp = await bot.get_msg(message_id=message_id)
        img_msg = resp["message"]

        image_found = False
        file_name = ""

        if not isinstance(img_msg, list):
            img_msg = [img_msg]

        for msg_part in img_msg:
            if isinstance(msg_part, dict) and msg_part.get("type") == "image":
                image_found = True
                data_part = msg_part.get("data", {})
                file_name_from_data = data_part.get("file")
                if file_name_from_data:
                    file_name = file_name_from_data
                    try:
                        image_info = await bot.call_api("get_image", file=file_name)
                        file_name = os.path.basename(image_info["file"])
                    except Exception as e:
                        logger.warning(
                            f"Failed to get image info via get_image API: {e}, "
                            f"using original file name: {file_name}"
                        )
                        file_name = os.path.basename(file_name_from_data)
                break

        if not image_found or not file_name:
            raise ReplyImageNotFoundException("未在回复消息中找到图片")

        return file_name
    except Exception as e:
        if isinstance(e, ReplyImageNotFoundException):
            raise
        logger.error(f"从消息ID提取图片失败: {e}", "群聊语录", e=e)
        raise ReplyImageNotFoundException(f"提取图片失败: {e}")


async def get_reply_message_id(event_json_str: str) -> int:
    """从事件JSON字符串中获取回复消息ID"""
    from .exceptions import ReplyImageNotFoundException

    try:
        if "reply" not in event_json_str:
            raise ReplyImageNotFoundException("未找到回复消息")

        try:
            event_data = json.loads(event_json_str)
            reply_info = event_data.get("reply")
            if (
                reply_info
                and isinstance(reply_info, dict)
                and "message_id" in reply_info
            ):
                return int(reply_info["message_id"])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        idx = event_json_str.find('"reply"')
        if idx == -1:
            idx = event_json_str.find("reply")

        temp_reply_id = ""
        id_field_idx = event_json_str.find('"message_id":', idx)
        if id_field_idx == -1:
            id_field_idx = event_json_str.find("id=", idx)

        if id_field_idx != -1:
            start_char_offset = event_json_str.find(":", id_field_idx) + 1
            if (
                event_json_str.find("id=", idx) != -1
                and event_json_str.find(":", id_field_idx) == -1
            ):
                start_char_offset = id_field_idx + 3

            for i in range(start_char_offset, len(event_json_str)):
                char = event_json_str[i]
                if char == '"':
                    continue
                if char != "-" and not char.isdigit():
                    break
                temp_reply_id += char
            reply_id = temp_reply_id.strip()

            if reply_id and reply_id.isdigit():
                return int(reply_id)

        raise ReplyImageNotFoundException("无法解析回复消息ID")
    except Exception as e:
        if isinstance(e, ReplyImageNotFoundException):
            raise
        logger.error(f"获取回复消息ID失败: {e}", "群聊语录", e=e)
        raise ReplyImageNotFoundException(f"获取回复消息ID失败: {e}")


async def extract_image_basename_from_reply(bot: Bot, event: MessageEvent | str) -> str:
    """提取回复中的图片文件名"""
    from .exceptions import ReplyImageNotFoundException

    if isinstance(event, MessageEvent):
        if not event.reply:
            raise ReplyImageNotFoundException("事件不包含回复消息")

        reply_message_id = event.reply.message_id
        return await extract_image_from_message_id(bot, reply_message_id)
    else:
        reply_id = await get_reply_message_id(event)
        return await extract_image_from_message_id(bot, reply_id)


def get_group_id_from_session(session_id: str) -> str | None:
    """从会话ID中提取群组ID"""
    if "group" in session_id:
        return session_id.split("_")[1]
    return None


async def reply_handle(
    bot: Bot,
    errMsg: str,
    event: MessageEvent | str,
    groupNum: str,
    user_id: str,
    listener: Any,
) -> str | None:
    """处理回复并提取图片"""
    from .exceptions import ReplyImageNotFoundException

    try:
        return await extract_image_basename_from_reply(bot, event)
    except ReplyImageNotFoundException:
        target = PlatformUtils.get_target(group_id=groupNum)
        at_msg = MessageUtils.build_message([At(target=user_id, flag="user"), errMsg])
        await at_msg.send(target=target, bot=bot)
        await listener.finish()
        return None
