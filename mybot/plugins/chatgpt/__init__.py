import nonebot
from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageSegment, GroupMessageEvent, PrivateMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.params import CommandArg
from loguru import logger
from datetime import datetime
import os
import asyncio
import aiohttp
import re
import random
import time
from nonebot.exception import FinishedException
from .config import config
from .image_to_text import image_to_text, clear_image_cache
from .utils import build_openai_request
from .database import Database
from .message_queue import MessageQueue

BOT_OWNER_ID = 123456  #这是bot主人的QQ号，用于权限控制，以及屏蔽相关的功能会完全不对主人进行作用

__plugin_meta__ = PluginMetadata(
    name="ChatGPT Plugin",
    description="通过HTTP请求调用OpenAI API的对话插件",
    usage=f"食用方法：\n堆堆+对话内容", # 使用方法, 会显示在 help 命令中
)

SEPARATOR = "|"    #这部分的"|"是AI再对话中加入这个符号即可实现对话分段发送
MIN_DELAY = 1111
MAX_DELAY = 3333   #DELAY是发送消息的延迟时间，单位是毫秒，分为最大范围和最小范围，可自定义

COOLDOWN_MODE = 'group'  # 'group' or 'user'，冷却模式，群组或用户
group_locks = {}
group_cache = {}
db = Database()
group_queues = {}
request_status = {}
user_block_status = {}  # 存储用户屏蔽状态

def is_user_blocked(user_id: int) -> bool:
    """
    检查用户是否在屏蔽状态
    """
    if user_id in user_block_status and time.time() < user_block_status[user_id]:
        logger.info(f"用户 {user_id} 正在被屏蔽，忽略消息")
        return True
    return False

@nonebot.get_driver().on_startup
async def startup():
    # 初始化全局数据库
    await db.init_db("global", True)

    # 初始化所有群聊和私聊的数据库
    for chat_id in group_queues.keys():
        is_group = "private_" not in chat_id
        await db.init_db(chat_id, is_group)

    asyncio.create_task(clear_image_cache())
    asyncio.create_task(clear_block_status())  # 启动时清理过期屏蔽状态任务


async def clear_block_status():   #这里的定期是每次bot重启都会清理一次过期的屏蔽状态
    """
    定期清理过期的屏蔽状态任务。
    """
    while True:
        current_time = time.time()
        for user_id in list(user_block_status.keys()):
            if current_time >= user_block_status[user_id]:
                del user_block_status[user_id]
        await asyncio.sleep(60)

message_handler = on_message(priority=5)

@message_handler.handle()
async def handle_message(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent):
    user_id = event.user_id
    is_group = isinstance(event, GroupMessageEvent)
    chat_id = event.group_id if is_group else f"private_{user_id}"

    # 检查用户是否在屏蔽状态
    if is_user_blocked(user_id):
        return

    if chat_id not in group_queues:
        group_queues[chat_id] = MessageQueue(chat_id, db, is_group=is_group)
        await db.init_db(chat_id, is_group)  # 确保数据库初始化
        await group_queues[chat_id].load_history()

    queue = group_queues[chat_id]
    
    if chat_id not in group_locks:
        group_locks[chat_id] = asyncio.Lock()
        group_cache[chat_id] = []

    lock = group_locks[chat_id]

    msg = event.get_message()
    text_content = ""

    async with lock:
        # 处理消息中的图片段和@段
        for seg in msg:
            if seg.type == "image":
                image_url = seg.data.get("url")
                if image_url:
                    text = await image_to_text(image_url)
                    text_content += f"[图片: {text}]"
            elif seg.type == "at":
                text_content += f"[at:qq={seg.data.get('qq')}]"
            elif seg.type == "text":
                text_content += seg.data.get("text")

        # 处理引用消息
        if event.reply:
            reply_message = event.reply.message
            reply_sender = event.reply.sender
            reply_time = event.reply.time
            formatted_reply_time = datetime.fromtimestamp(reply_time).strftime('%Y-%m-%d %H:%M:%S')
            reply_user_info = f"{reply_sender.nickname} ({reply_sender.user_id})"

            for seg in reply_message:
                if seg.type == "image":
                    image_url = seg.data.get("url")
                    if image_url:
                        text = await image_to_text(image_url)
                        text_content += f"[引用图片: {formatted_reply_time} {reply_user_info}: {text}]"
                elif seg.type == "text":
                    text = seg.data.get("text")
                    text_content += f"[引用文字: {formatted_reply_time} {reply_user_info}: {text}]"
                elif seg.type == "at":
                    text_content += f"[at:qq={seg.data.get('qq')}]"

        formatted_time = datetime.fromtimestamp(event.time).strftime('%Y-%m-%d %H:%M:%S')
        direction = "<<<收到消息于群聊" if is_group else "<<<收到私聊"
        new_msg = {
            "direction": direction,
            "user_id": f"{user_id}",
            "user_name": f"{event.sender.nickname}",
            "content": text_content
        }
        await queue.add_message(new_msg, event.time)
        logger.info(f"文字消息已加入队列：{new_msg}")
        logger.debug(f"handle_message - 用户输入内容处理后: {text_content}")



oachat = on_command(config.oachat_on_command, aliases={"堆堆"}, block=True, priority=5)
oachat_help = on_command("/help", aliases={"/帮助"}, block=True, priority=5)

@oachat_help.handle()
async def show_help():
    await oachat_help.finish(__plugin_meta__.usage)

@oachat.handle()
async def handle_chat(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent, msg: Message = CommandArg()):
    logger.debug("handle_chat triggered")
    
    if COOLDOWN_MODE == 'user':
        key = event.user_id
    else:
        key = event.group_id if isinstance(event, GroupMessageEvent) else f"private_{event.user_id}"

    if key != BOT_OWNER_ID:
        if request_status.get(key, False) or is_user_blocked(event.user_id):
            return

    request_status[key] = True
    
    try:
        original_msg = msg.extract_plain_text().strip()
        logger.debug(f"handle_chat - 原始用户输入内容: {original_msg}")

        group_id = event.group_id if isinstance(event, GroupMessageEvent) else f"private_{event.user_id}"
        if group_id not in group_queues:
            group_queues[group_id] = MessageQueue(group_id, db, is_group=isinstance(event, GroupMessageEvent))
            await group_queues[group_id].load_history()

        queue = group_queues[group_id]

        if group_id not in group_locks:
            group_locks[group_id] = asyncio.Lock()
            group_cache[group_id] = []

        lock = group_locks[group_id]
        cache = group_cache[group_id]

        async with lock:
            history_messages = queue.get_messages()
            formatted_history = "\n".join(history_messages)

            # 直接使用 event.message 的内容构建 current_input
            current_input = "".join(seg.data.get("text", "") for seg in event.message)

            user_info = {
                "user_id": event.user_id,
                "nickname": event.sender.nickname
            }

            logger.debug(f"handle_chat - 初始 current_input: {current_input}")

            # 处理消息中的图片段和@段
            for seg in event.message:
                if seg.type == "image":
                    image_url = seg.data.get("url")
                    if image_url:
                        text = await image_to_text(image_url)
                        current_input += f" [图片: {text}]"
                elif seg.type == "at":
                    current_input += f"[at:qq={seg.data.get('qq')}]"

            logger.debug(f"handle_chat - 处理段落后的 current_input: {current_input}")

            # 处理引用消息
            if event.reply:
                reply_message = event.reply.message
                reply_sender = event.reply.sender
                reply_time = event.reply.time
                formatted_reply_time = datetime.fromtimestamp(reply_time).strftime('%Y-%m-%d %H:%M:%S')
                reply_user_info = f"{reply_sender.nickname} ({reply_sender.user_id})"

                for seg in reply_message:
                    if seg.type == "image":
                        image_url = seg.data.get("url")
                        if image_url:
                            text = await image_to_text(image_url)
                            current_input += f" [引用图片: {formatted_reply_time} {reply_user_info}: {text}]"
                    elif seg.type == "text":
                        text = seg.data.get("text")
                        current_input += f" [引用文字: {formatted_reply_time} {reply_user_info}: {text}]"
                    elif seg.type == "at":
                        current_input += f"[at:qq={seg.data.get('qq')}]"

            logger.debug(f"handle_chat - 处理引用消息后的 current_input: {current_input}")

            context = (
                f"以下是群里的历史记录内容\n----------\n{formatted_history}\n----------"
                f"你的名字叫堆堆，你的QQ号是234567\n"                  #这里是内置提示词部分，可以自定义
                f"\n当前对话的用户名是{user_info['nickname']}，QQ号{user_info['user_id']}，一定要看清ta的名字和QQ号哦，请不要认错人哦！\n----------\n用户{user_info['nickname']}，QQ号{user_info['user_id']}发送消息：\n|{current_input}|\n"
            )

            logger.debug(f"handle_chat - 构建的上下文内容: {context}")

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.openai_api_key}"
            }
            data = build_openai_request(context, config.openai_max_tokens)

            max_retries = 1  # 自定义重试次数
            retry_count = 0
            reply = ""

            while retry_count < max_retries:
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.post(config.api_url, headers=headers, json=data) as response:
                            result = await response.json()
                            if response.status == 200:
                                reply = result["choices"][0]["message"]["content"].strip()
                                logger.debug(f"handle_chat - OpenAI回复内容: {reply}")

                                # 如果回复不为空，跳出循环
                                if reply:
                                    break
                            else:
                                logger.error(f"请求失败: {result.get('error', {}).get('message', '未知错误')}")
                    except aiohttp.ClientError as e:
                        logger.error(f"HTTP请求出错: {str(e)}")
                    except asyncio.TimeoutError:
                        logger.error("请求超时")
                    except Exception as e:
                        logger.error(f"未知异常: {str(e)}")

                # 如果回复为空，增加重试次数
                retry_count += 1
                logger.info(f"AI回复为空，重试第 {retry_count} 次")

                # 等待一段时间后重试，避免频繁请求
                await asyncio.sleep(1)

            # 如果重试达到最大次数仍然为空，则返回自定义的固定回复
            if not reply:
                reply = "..."

            # 处理屏蔽指令
            block_pattern = re.compile(r"屏蔽(\d+)&(\d+)(秒|分钟|小时)")
            matches = block_pattern.findall(reply)
            for match in matches:
                user_id = int(match[0])
                duration = int(match[1])
                unit = match[2]
                if user_id != BOT_OWNER_ID:
                    block_time = duration
                    if unit == "分钟":
                        block_time *= 60
                    elif unit == "小时":
                        block_time *= 3600
                    new_block_end_time = time.time() + block_time
                    current_block_end_time = user_block_status.get(user_id, 0)
                    if new_block_end_time > current_block_end_time:
                        user_block_status[user_id] = new_block_end_time
                    # 替换屏蔽指令
                    reply = re.sub(rf"\[屏蔽用户{user_id}\s+{duration}{unit}\]", f"[屏蔽用户{user_id} {duration}{unit}]", reply)

            bot_name = bot.config.nickname if bot.config.nickname else "堆堆"
            formatted_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            new_msg = f"[{formatted_time}] [{bot.self_id}] [{bot_name}]: {reply}"

            # 替换重复的 self_id
            new_msg = new_msg.replace(f"[{bot.self_id}] [{bot.self_id}]", f"[{bot.self_id}] [{bot_name}]")

            await queue.add_message({
                "direction": ">>>发送消息至群聊" if isinstance(event, GroupMessageEvent) else ">>>发送私聊",
                "user_id": event.user_id,
                "user_name": event.sender.nickname,
                "content": reply
            }, int(datetime.now().timestamp()))
            logger.info(f"AI回复消息已加入队列：{new_msg}")

            # 过滤连续分隔符和首尾分隔符
            segments = reply.split(SEPARATOR)
            segments = [seg for seg in segments if seg.strip()]
            segments = [seg.strip(SEPARATOR) for seg in segments]

            for segment in segments:
                await bot.send(event, segment)
                # 随机延迟
                delay = random.randint(MIN_DELAY, MAX_DELAY) / 1000.0
                await asyncio.sleep(delay)
    finally:
        if key != BOT_OWNER_ID:
            request_status[key] = False
    
    while cache:
        msg = cache.pop(0)
        await handle_group_message(bot, msg)



# 新增清除全部记录指令
clear_all_memory = on_command("/清除全部记忆", block=True, priority=5)

@clear_all_memory.handle()
async def handle_clear_all_memory(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent):
    if event.user_id != BOT_OWNER_ID:
        await clear_all_memory.finish("你没有权限执行此操作。")

    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        await db.clear_group_messages(str(group_id))
        
        if group_id in group_queues:
            group_queues[group_id] = MessageQueue(group_id, db, is_group=True)
            await group_queues[group_id].load_history()
        
        await clear_all_memory.finish("已清除该群的全部记忆。")
    else:
        await clear_all_memory.finish("私聊不支持清除全部记忆。")

# 新增清除一定数量记录指令
clear_some_memory = on_command("/清除记忆", block=True, priority=5)

@clear_some_memory.handle()
async def handle_clear_some_memory(bot: Bot, event: GroupMessageEvent | PrivateMessageEvent, msg: Message = CommandArg()):
    if event.user_id != BOT_OWNER_ID:
        await clear_some_memory.finish("你没有权限执行此操作。")

    try:
        num_to_clear = int(msg.extract_plain_text().strip())
    except ValueError:
        await clear_some_memory.finish("请输入有效的数字。")

    if isinstance(event, GroupMessageEvent):
        group_id = event.group_id
        await db.delete_latest_group_messages(str(group_id), num_to_clear)
        
        if group_id in group_queues:
            group_queues[group_id] = MessageQueue(group_id, db, is_group=True)
            await group_queues[group_id].load_history()
        
        await clear_some_memory.finish(f"已清除该群的最新{num_to_clear}条记忆。")
    else:
        await clear_some_memory.finish("私聊不支持清除部分记忆。")

# 新增屏蔽列表指令
block_list = on_command("/屏蔽列表", block=True, priority=5)

@block_list.handle()
async def handle_block_list(bot: Bot, event: GroupMessageEvent):
    if not user_block_status:
        await block_list.finish("当前没有被屏蔽的用户。")

    current_time = time.time()
    block_info = []
    for user_id, end_time in user_block_status.items():
        remaining_time = int(end_time - current_time)
        if remaining_time > 0:
            hours, remainder = divmod(remaining_time, 3600)
            minutes, seconds = divmod(remainder, 60)
            time_str = f"{hours}小时{minutes}分钟{seconds}秒"
            block_info.append(f"用户 {user_id} 剩余屏蔽时间：{time_str}")

    if block_info:
        await block_list.finish("\n".join(block_info))
    else:
        await block_list.finish("当前没有被屏蔽的用户。")


# 新增屏蔽用户指令
block_user = on_command("/屏蔽", block=True, priority=5)

@block_user.handle()
async def handle_block_user(bot: Bot, event: GroupMessageEvent, msg: Message = CommandArg()):
    if event.user_id != BOT_OWNER_ID:
        await block_user.finish("你没有权限执行此操作。")

    args = msg.extract_plain_text().strip().split()
    if len(args) != 2:
        await block_user.finish("格式错误，请使用：/屏蔽 用户ID 时间(秒/分钟/小时)")

    user_id, duration_str = args
    try:
        user_id = int(user_id)
    except ValueError:
        await block_user.finish("用户ID格式错误，请输入有效的数字。")

    match = re.match(r"(\d+)(秒|分钟|小时)", duration_str)
    if not match:
        await block_user.finish("时间格式错误，请使用：数字+秒/分钟/小时")

    duration, unit = match.groups()
    duration = int(duration)
    if unit == "分钟":
        duration *= 60
    elif unit == "小时":
        duration *= 3600

    user_block_status[user_id] = time.time() + duration
    await block_user.finish(f"已屏蔽用户 {user_id} {match.group()}。")


# 新增解除屏蔽用户指令
unblock_user = on_command("/解除屏蔽", block=True, priority=5)

# 新增解除所有屏蔽用户指令
unblock_all_user = on_command("/解除所有屏蔽", block=True, priority=5)

@unblock_user.handle()
async def handle_unblock_user(bot: Bot, event: GroupMessageEvent, msg: Message = CommandArg()):
    if event.user_id != BOT_OWNER_ID:
        await unblock_user.finish("你没有权限执行此操作。")

    user_id = msg.extract_plain_text().strip()
    try:
        user_id = int(user_id)
    except ValueError:
        await unblock_user.finish("用户ID格式错误，请输入有效的数字。")

    if user_id in user_block_status:
        del user_block_status[user_id]
        await unblock_user.finish(f"已解除对用户 {user_id} 的屏蔽。")
    else:
        await unblock_user.finish(f"用户 {user_id} 未被屏蔽。")

@unblock_all_user.handle()
async def handle_unblock_all_user(bot: Bot, event: GroupMessageEvent):
    if event.user_id != BOT_OWNER_ID:
        await unblock_all_user.finish("你没有权限执行此操作。")

    user_block_status.clear()  # 清空屏蔽列表
    await unblock_all_user.finish("已解除所有用户的屏蔽。")

