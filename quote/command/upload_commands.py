import asyncio
import hashlib
import os
import re
import shutil
import uuid

import aiofiles
from arclet.alconna import Alconna, AllParam, Args, Arparma
import httpx
from nonebot.adapters.onebot.v11 import Bot, Event, MessageEvent, MessageSegment
from nonebot.permission import SUPERUSER
from nonebot.typing import T_State
from nonebot_plugin_alconna import on_alconna
from nonebot_plugin_alconna.uniseg import Image as UniImage

from zhenxun.services.log import logger

from ..config import get_author_font_path, get_font_path, get_quote_path, ensure_quote_path
from ..services.ocr_service import OCRService
from ..services.quote_service import QuoteService
from ..utils.exceptions import ImageProcessError, NetworkError
from ..utils.image_utils import copy_images_files, download_qq_avatar, get_img_hash
from ..utils.message_utils import get_group_id_from_session, send_group_message

upload_alc = Alconna("上传", Args["image?", UniImage])
save_img_cmd = on_alconna(upload_alc, auto_send_output=False, block=True)

make_record_alc = Alconna("记录")
make_record_cmd = on_alconna(make_record_alc, block=True)

render_quote_alc = Alconna("生成")
render_quote_cmd = on_alconna(render_quote_alc, block=True)

batch_upload_alc = Alconna("batch_upload", Args["content?", AllParam])
script_batch_cmd = on_alconna(batch_upload_alc, permission=SUPERUSER, block=True)

copy_batch_alc = Alconna("batch_copy", Args["content?", AllParam])
copy_batch_cmd = on_alconna(copy_batch_alc, permission=SUPERUSER, block=True)


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

    ocr_content = await OCRService.recognize_text(temp_image_path)

    if "group" in session_id:
        group_id = session_id.split("_")[1]

        duplicate = await QuoteService.check_duplicate_by_hash(group_id, image_hash)

        if duplicate:
            logger.info("发现重复图片，不保存新图片", "群聊语录")

            if temp_image_path != image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                    logger.info(f"删除临时文件: {temp_image_path}", "群聊语录")
                except Exception as e:
                    logger.error(f"删除临时文件失败: {e}", "群聊语录", e=e)

            quote = duplicate
            is_new = False
        else:
            image_name = hashlib.md5(img_data).hexdigest() + ".png"
            final_image_path = os.path.abspath(os.path.join(get_quote_path(), image_name))

            if temp_image_path != final_image_path:
                async with aiofiles.open(final_image_path, "wb") as f:
                    await f.write(img_data)

                if temp_image_path != image_path and os.path.exists(temp_image_path):
                    try:
                        os.remove(temp_image_path)
                        logger.info(f"删除临时文件: {temp_image_path}", "群聊语录")
                    except Exception as e:
                        logger.error(f"删除临时文件失败: {e}", "群聊语录", e=e)

            logger.info(f"图片已保存到 {final_image_path}", "群聊语录")

            quote, is_new = await QuoteService.add_quote(
                group_id=group_id,
                image_path=final_image_path,
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
    bot: Bot, event: MessageEvent, arp: Arparma, state: T_State
):
    """记录语录处理函数"""

    qqid = None
    raw_message = ""
    card = ""
    session_id = event.get_session_id()
    user_id = str(event.get_user_id())

    if event.reply:
        qqid = event.reply.sender.user_id
        raw_message = event.reply.message.extract_plain_text().strip()
        card = (
            event.reply.sender.card
            if event.reply.sender.card
            else event.reply.sender.nickname
        )
    else:
        await make_record_cmd.finish("请回复所需的消息")
        return

    if str(qqid) == user_id:
        await make_record_cmd.finish("不能记录自己的消息")
        return

    if not raw_message:
        await make_record_cmd.send("空内容")
        return

    try:
        data = await download_qq_avatar(qqid)
        group_id = get_group_id_from_session(session_id)

        if group_id:
            recorded_text = card + " " + raw_message

            img_data = await QuoteService.generate_temp_quote(
                avatar_bytes=data,
                text=raw_message,
                author=card,
                font_path=get_font_path(),
                author_font_path=get_author_font_path(),
                save_to_file=False,
            )

            duplicate_by_text = await QuoteService.check_duplicate_by_text(
                group_id, recorded_text, str(qqid)
            )

            if duplicate_by_text:
                logger.info("发现重复文本，不保存新图片", "群聊语录")
                quote = duplicate_by_text
                is_new = False
            else:
                image_name = hashlib.md5(img_data).hexdigest() + ".png"
                image_path = os.path.abspath(os.path.join(get_quote_path(), image_name))

                async with aiofiles.open(image_path, "wb") as file:
                    await file.write(img_data)

                quote, is_new = await QuoteService.add_quote(
                    group_id=group_id,
                    image_path=image_path,
                    ocr_content=None,
                    recorded_text=recorded_text,
                    uploader_user_id=user_id,
                    quoted_user_id=str(qqid),
                )

            if quote:
                if is_new:
                    msg_to_send = MessageSegment.image(img_data)
                    await send_group_message(bot, group_id, msg_to_send)
                else:
                    await send_group_message(bot, group_id, "不要重复记录")
            else:
                await send_group_message(bot, group_id, "保存语录失败，数据库错误")
        else:
            img_data = await QuoteService.generate_temp_quote(
                avatar_bytes=data,
                text=raw_message,
                author=card,
                font_path=get_font_path(),
                author_font_path=get_author_font_path(),
                save_to_file=False,
            )
            msg_to_send = MessageSegment.image(img_data)
            await bot.send_private_msg(
                user_id=int(event.get_user_id()), message=msg_to_send
            )
    except NetworkError as e:
        await make_record_cmd.finish(str(e))
    except ImageProcessError as e:
        await make_record_cmd.finish(str(e))
    except Exception as e:
        logger.error(f"生成语录图片失败: {e}", "群聊语录", e=e)
        await make_record_cmd.finish(f"生成语录图片失败: {e}")


@render_quote_cmd.handle()
async def render_quote_handle(
    bot: Bot, event: MessageEvent, arp: Arparma, state: T_State
):
    """生成语录处理函数"""

    qqid = None
    raw_message = ""
    card = ""
    session_id = event.get_session_id()

    if event.reply:
        qqid = event.reply.sender.user_id
        raw_message = event.reply.message.extract_plain_text().strip()
        card = (
            event.reply.sender.card
            if event.reply.sender.card
            else event.reply.sender.nickname
        )
    else:
        await render_quote_cmd.finish("请回复所需的消息")
        return

    if not raw_message:
        await render_quote_cmd.send("空内容")
        return

    try:
        data = await download_qq_avatar(qqid)

        img_data = await QuoteService.generate_temp_quote(
            avatar_bytes=data,
            text=raw_message,
            author=card,
            font_path=get_font_path(),
            author_font_path=get_author_font_path(),
            save_to_file=True,
        )

        msg_to_send = MessageSegment.image(img_data)

        group_id = get_group_id_from_session(session_id)

        if group_id:
            await send_group_message(bot, group_id, msg_to_send)
        else:
            await bot.send_private_msg(
                user_id=int(event.get_user_id()), message=msg_to_send
            )
    except NetworkError as e:
        await render_quote_cmd.finish(str(e))
    except ImageProcessError as e:
        await render_quote_cmd.finish(str(e))
    except Exception as e:
        logger.error(f"生成语录图片失败: {e}", "群聊语录", e=e)
        await render_quote_cmd.finish(f"生成语录图片失败: {e}")


@script_batch_cmd.handle()
async def script_batch_handle(bot: Bot, event: Event, arp: Arparma, state: T_State):
    """批量上传语录处理函数"""
    session_id = event.get_session_id()
    user_id = str(event.get_user_id())

    if "group" not in session_id:
        await script_batch_cmd.finish("该功能暂不支持私聊")
        return

    group_id = session_id.split("_")[1]

    content_list: list[str] = arp.all_matched_args.get("content", [])
    raw_msg = "\n".join(content_list) if content_list else ""

    qqgroup_match = re.search(r"qqgroup=([^\n\s]+)", raw_msg)
    your_path_match = re.search(r"your_path=([^\n\s]+)", raw_msg)
    gocq_path_match = re.search(r"gocq_path=([^\n\s]+)", raw_msg)
    tags_match = re.search(r"tags=([^\n]+)", raw_msg)

    group_id_list = [qqgroup_match.group(1)] if qqgroup_match else []
    your_path_list = [your_path_match.group(1)] if your_path_match else []
    gocq_path_list = [gocq_path_match.group(1)] if gocq_path_match else []
    tags_list_parsed = [tags_match.group(1).strip()] if tags_match else []

    instruction = """指令如下:
batch_upload
qqgroup=123456
your_path=/home/xxx/images
gocq_path=/home/xxx/gocq/data/cache
tags=aaa bbb ccc"""
    if not group_id_list or not your_path_list or not gocq_path_list:
        await script_batch_cmd.finish(instruction)
        return

    target_group_id_str = group_id_list[0]
    your_path_str = your_path_list[0]
    gocq_path_str = gocq_path_list[0]

    image_files = await copy_images_files(your_path_str, gocq_path_str)
    total_len = len(image_files)
    idx = 0

    for _, img_rel_path in image_files:
        img_full_path = os.path.join(gocq_path_str, img_rel_path)
        save_file = os.path.abspath(img_full_path)

        idx += 1
        try:
            logger.info(f"尝试使用绝对路径发送图片: {save_file}")
            await bot.send_msg(
                group_id=int(group_id),
                message=MessageSegment.image(f"file:///{save_file}"),
            )
            logger.info(f"图片 {img_rel_path} 发送成功 (for preview)")
        except Exception as send_err:
            logger.error(
                f"预览图片 {img_rel_path} (路径: {save_file}) 发送失败: {send_err}",
                exc_info=True,
            )
            await bot.send_msg(
                group_id=int(group_id),
                message=f"图片 {img_rel_path} 预览发送失败，跳过此图处理。",
            )
            continue

        await asyncio.sleep(1)

        image_hash = await get_img_hash(save_file)

        duplicate = await QuoteService.check_duplicate_by_hash(
            target_group_id_str, image_hash
        )

        if duplicate:
            logger.info(
                f"图片 {save_file} 已存在于群 {target_group_id_str} 的数据库中，跳过。"
            )
            await bot.send_msg(
                group_id=int(group_id), message="上述图片已存在于目标语录库"
            )
            continue
        else:
            logger.info(
                f"图片 {save_file} 不在群 {target_group_id_str} 的数据库中，继续处理。"
            )

        ocr_content = await OCRService.recognize_text(save_file)

        quote, is_new = await QuoteService.add_quote(
            group_id=target_group_id_str,
            image_path=save_file,
            ocr_content=ocr_content,
            recorded_text=None,
            uploader_user_id=user_id,
            image_hash=image_hash,
        )

        if quote:
            if is_new:
                logger.info(
                    f"图片 {save_file} 已添加到群 {target_group_id_str} 的数据库中。"
                )

                if tags_list_parsed:
                    tag_list_for_image = tags_list_parsed[0].strip().split(" ")
                    success = await QuoteService.add_tags(quote, tag_list_for_image)
                    if success:
                        logger.info(
                            f"为图片 {save_file} 添加标签: {tag_list_for_image}"
                        )
                    else:
                        logger.warning(f"为图片 {save_file} 添加标签失败")
            else:
                logger.warning(
                    f"图片 {save_file} 已存在于群 {target_group_id_str} 的数据库中，跳过。"
                )
                continue
        else:
            logger.error(f"图片 {save_file} 添加到数据库失败")
            await bot.send_msg(
                group_id=int(group_id),
                message=f"图片 {img_rel_path} 添加到数据库失败，跳过。",
            )
            continue

        if idx % 10 == 0:
            logger.info(f"处理进度 {idx}/{total_len}")
            await bot.send_msg(
                group_id=int(group_id), message=f"当前进度{idx}/{total_len}"
            )
            await asyncio.sleep(1)

    await bot.send_msg(group_id=int(group_id), message="批量导入完成")


@copy_batch_cmd.handle()
async def copy_batch_handle(bot: Bot, event: Event, arp: Arparma, state: T_State):
    """批量备份语录处理函数"""
    content_list: list[str] = arp.all_matched_args.get("content", [])
    raw_msg = "\n".join(content_list) if content_list else ""

    your_path_match = re.search(r"your_path=([^\n\s]+)", raw_msg)
    your_path_list = [your_path_match.group(1)] if your_path_match else []

    instruction = """指令如下:
batch_copy
your_path=/home/xxx/images"""
    if not your_path_list:
        await copy_batch_cmd.finish(instruction)
        return

    your_path_str = your_path_list[0]

    try:
        all_quotes = await QuoteService.get_all_quotes()

        if not all_quotes:
            await copy_batch_cmd.finish("数据库中没有语录")
            return

        for quote in all_quotes:
            img_full_path = quote.image_path
            if not os.path.exists(img_full_path):
                logger.warning(
                    f"Source image {img_full_path} not found, skipping copy."
                )
                continue

            img_basename = os.path.basename(img_full_path)
            destination_full_path = os.path.join(your_path_str, img_basename)

            shutil.copyfile(img_full_path, destination_full_path)
            logger.info(f"Copied {img_full_path} to {destination_full_path}")

    except FileNotFoundError:
        await copy_batch_cmd.finish("路径不正确或文件不存在")
        return
    except Exception as e:
        logger.error(f"批量备份发生错误: {e}", exc_info=True)
        await copy_batch_cmd.finish(f"备份过程中发生错误: {e}")
        return

    await copy_batch_cmd.finish("备份完成")
