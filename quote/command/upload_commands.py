import hashlib
import os
import uuid

import aiofiles
from arclet.alconna import Alconna, Args, Arparma, Option
import httpx
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, MessageSegment
from nonebot.typing import T_State
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna.uniseg import Image as UniImage, UniMessage
from nonebot_plugin_alconna.uniseg.tools import reply_fetch
from nonebot_plugin_uninfo import Uninfo

from zhenxun.services.log import logger
from zhenxun.utils.message import MessageUtils

from ..config import ensure_quote_path
from ..services.ocr_service import OCRService
from ..services.quote_service import QuoteService
from ..utils.exceptions import ImageProcessError, NetworkError
from ..utils.image_utils import download_qq_avatar, get_img_hash

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

    replied_message = await UniMessage.generate(message=reply.msg, bot=bot)
    raw_message = replied_message.extract_plain_text().strip()
    if not raw_message:
        return None, "回复的消息内容为空。"

    sender = event.reply.sender
    qqid = sender.user_id
    card = sender.card or sender.nickname or str(qqid)

    return (raw_message, card, qqid), None

async def _generate_quote_from_reply(event: MessageEvent, bot: Bot, style_name: str | None = None):
    """
    从回复消息中提取信息并生成语录图片。
    这是一个辅助函数，用于合并 make_record 和 render_quote 的公共逻辑。
    """
    info, error = await _extract_info_from_reply(event, bot)
    if error:
        return None, error

    raw_message, card, qqid = info

    try:
        avatar_data = await download_qq_avatar(qqid)

        img_data = await QuoteService.generate_temp_quote(
            avatar_bytes=avatar_data,
            text=raw_message,
            author=card,
            save_to_file=False,
            style_name=style_name,
        )
        return (img_data, card, raw_message, qqid), None
    except (NetworkError, ImageProcessError) as e:
        return None, str(e)
    except Exception as e:
        logger.error(f"生成语录图片时发生未知错误: {e}", "群聊语录", e=e)
        return None, f"生成语录图片时发生未知错误: {e}"


upload_alc = Alconna("上传", Args["image?", UniImage])
save_img_cmd = on_alconna(upload_alc, auto_send_output=False, block=True)
make_record_alc = Alconna("记录", Args(), Option("-s|--style", Args["style_name", str], help_text="指定主题样式"))
make_record_cmd = on_alconna(make_record_alc, block=True)

generate_quote_alc = Alconna("生成", Args(), Option("-s|--style", Args["style_name", str], help_text="指定主题样式"))
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


@make_record_cmd.handle()
async def make_record_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, session: Uninfo
):
    """记录语录处理函数 (重构后，先检查后渲染)"""
    info, error = await _extract_info_from_reply(event, bot)
    if error:
        await make_record_cmd.finish(error)
        return

    raw_message, card, qqid = info
    user_id = session.user.id

    if str(qqid) == user_id:
        await make_record_cmd.finish("不能记录自己的消息")
        return

    if session.group:
        recorded_text = f"{card} {raw_message}"
        is_duplicate = await QuoteService.check_duplicate_text_quote(
            group_id=session.group.id,
            recorded_text=recorded_text,
            quoted_user_id=str(qqid),
        )
        if is_duplicate:
            await MessageUtils.build_message("不要重复记录").send(target=event, bot=bot)
            return

    style_name: str | None = arp.query("style.style_name")
    if style_name:
        logger.info(f"用户请求使用主题: {style_name}", "群聊语录")

    try:
        avatar_data = await download_qq_avatar(qqid)
        img_data = await QuoteService.generate_temp_quote(
            avatar_bytes=avatar_data,
            text=raw_message,
            author=card,
            save_to_file=False,
            style_name=style_name,
        )
    except (NetworkError, ImageProcessError) as e:
        await make_record_cmd.finish(f"生成语录图片时出错: {e}")
        return
    except Exception as e:
        logger.error(f"生成语录图片时发生未知错误: {e}", "群聊语录", e=e)
        await make_record_cmd.finish(f"生成语录图片时发生未知错误: {e}")
        return

    if session.group:
        image_name = hashlib.md5(img_data).hexdigest() + ".png"
        image_path = ensure_quote_path() / image_name

        async with aiofiles.open(image_path, "wb") as file:
            await file.write(img_data)

        quote, is_new = await QuoteService.add_quote(
            group_id=session.group.id,
            image_path=str(image_path),
            ocr_content=None,
            recorded_text=recorded_text,
            uploader_user_id=user_id,
            quoted_user_id=str(qqid),
        )

        if quote and is_new:
            await MessageUtils.build_message(img_data).send(target=event, bot=bot)
        else:
            if os.path.exists(image_path):
                os.remove(image_path)
            await MessageUtils.build_message("保存语录时发生意外，请稍后再试").send(
                target=event, bot=bot
            )
    else:
        await MessageUtils.build_message(img_data).send(target=event, bot=bot)


@generate_quote_cmd.handle()
async def generate_quote_handle(bot: Bot, event: MessageEvent, arp: Arparma):
    """
    生成语录处理函数。
    只生成图片并发送，不进行任何存储或数据库操作。
    """
    style_name: str | None = arp.query("style.style_name")
    if style_name:
        logger.info(f"用户请求使用主题 '{style_name}' 生成临时语录", "群聊语录")

    result, error = await _generate_quote_from_reply(event, bot, style_name)

    if error:
        await generate_quote_cmd.finish(error)
        return

    img_data, _, _, _ = result

    await MessageUtils.build_message(img_data).send(target=event, bot=bot)