import asyncio
from loguru import logger
from .database import Database
from .config import config
from datetime import datetime

class MessageQueue:
    def __init__(self, id: str, db: Database, is_group: bool = True):
        self.id = id
        self.db = db
        self.is_group = is_group
        self.max_size = config.oachat_queue_size_group if is_group else config.oachat_queue_size_private
        self.buffer = []
        self.lock = asyncio.Lock()

    async def load_history(self):
        messages = await self.db.get_messages(self.id, self.max_size, self.is_group)
        self.buffer.extend(messages)
        logger.info(f"Loaded {len(messages)} messages for {'group' if self.is_group else 'private chat'} {self.id}")

    async def add_message(self, message: dict, time: int):
        async with self.lock:
            message_data = {
                "timestamp": time,
                "bot_id": "234567",        #这里是队列记录bot本身发送消息，建议将id和name都改成自己bot的和前面统一，不然会错乱
                "bot_name": "堆堆",
                "direction": message.get("direction", ""),
                "chat_id": self.id,
                "user_id": message.get("user_id", ""),
                "user_name": message.get("user_name", ""),
                "content": message.get("content", "")
            }
            await self.db.add_message(self.id, message_data, time, self.is_group)
            self.buffer.append(message_data)
            if len(self.buffer) > self.max_size:
                self.buffer.pop(0)
            logger.debug(f"Message added to queue: {message_data}")

    def get_messages(self):
        return [self.format_message(msg) for msg in self.buffer]

    def format_message(self, message: dict) -> str:
        formatted_time = datetime.fromtimestamp(message['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        return f"[{formatted_time}] [你的号码: {message['bot_id']}] [你的名称: {message['bot_name']}] [{message['direction']} {message['chat_id']}] [对方号码: {message['user_id']}] [对方名称: {message['user_name']}]: {message['content']}"

