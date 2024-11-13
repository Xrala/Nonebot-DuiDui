import aiosqlite
import asyncio
import os

GROUP_DB_DIR = "database/groups"
PRIVATE_DB_DIR = "database/private"

# 这个类是用来处理数据库的，主要是用来存储消息的，至少现在可以使用，再改我也看不懂了

class Database:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.ensure_db_path_exists()

    def ensure_db_path_exists(self):
        os.makedirs(GROUP_DB_DIR, exist_ok=True)
        os.makedirs(PRIVATE_DB_DIR, exist_ok=True)

    def get_db_path(self, id: str, is_group: bool) -> str:
        if is_group:
            return os.path.join(GROUP_DB_DIR, f"{id}.db")
        else:
            return os.path.join(PRIVATE_DB_DIR, f"{id}.db")

    async def init_db(self, id: str, is_group: bool):
        db_path = self.get_db_path(id, is_group)
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER,
                    bot_id TEXT,
                    bot_name TEXT,
                    direction TEXT,
                    chat_id TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    message TEXT
                )
            """)
            await db.commit()

    async def add_message(self, id: str, message: dict, time: int, is_group: bool):
        db_path = self.get_db_path(id, is_group)
        async with self.lock:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("""
                    INSERT INTO messages (timestamp, bot_id, bot_name, direction, chat_id, user_id, user_name, message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (time, message["bot_id"], message["bot_name"], message["direction"], message["chat_id"], message["user_id"], message["user_name"], message["content"]))
                await db.commit()

    async def get_messages(self, id: str, limit: int, is_group: bool):
        db_path = self.get_db_path(id, is_group)
        async with self.lock:
            async with aiosqlite.connect(db_path) as db:
                await self.ensure_table_exists(db)
                cursor = await db.execute("""
                    SELECT timestamp, bot_id, bot_name, direction, chat_id, user_id, user_name, message FROM messages
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                rows = await cursor.fetchall()
                return [self.ensure_message_keys(dict(zip([column[0] for column in cursor.description], row))) for row in rows[::-1]]  # 按时间顺序返回消息

    async def ensure_table_exists(self, db):
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER,
                bot_id TEXT,
                bot_name TEXT,
                direction TEXT,
                chat_id TEXT,
                user_id TEXT,
                user_name TEXT,
                message TEXT
            )
        """)
        await db.commit()

    def ensure_message_keys(self, message: dict) -> dict:
        keys = ["timestamp", "bot_id", "bot_name", "direction", "chat_id", "user_id", "user_name", "content"]
        for key in keys:
            if key not in message:
                message[key] = ""
        return message

    async def clear_group_messages(self, group_id: str):
        db_path = self.get_db_path(group_id, is_group=True)
        async with self.lock:
            async with aiosqlite.connect(db_path) as db:
                await self.ensure_table_exists(db)
                await db.execute("DELETE FROM messages")
                await db.commit()

    async def clear_private_messages(self, user_id: str):
        db_path = self.get_db_path(user_id, is_group=False)
        async with self.lock:
            async with aiosqlite.connect(db_path) as db:
                await self.ensure_table_exists(db)
                await db.execute("DELETE FROM messages")
                await db.commit()

    async def delete_latest_private_messages(self, user_id: str, limit: int):
        db_path = self.get_db_path(user_id, is_group=False)
        async with self.lock:
            async with aiosqlite.connect(db_path) as db:
                await self.ensure_table_exists(db)
                cursor = await db.execute("""
                    SELECT id FROM messages
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                rows = await cursor.fetchall()
                message_ids = [row[0] for row in rows]
                for message_id in message_ids:
                    await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                await db.commit()

    async def delete_latest_group_messages(self, group_id: str, limit: int):
        db_path = self.get_db_path(group_id, is_group=True)
        async with self.lock:
            async with aiosqlite.connect(db_path) as db:
                await self.ensure_table_exists(db)
                cursor = await db.execute("""
                    SELECT id FROM messages
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                rows = await cursor.fetchall()
                message_ids = [row[0] for row in rows]
                for message_id in message_ids:
                    await db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
                await db.commit()

