"""
database.py
Thin wrapper around Motor (async MongoDB driver) for storing per-guild config.

Document shape, collection "guild_configs":
{
    "_id": <guild_id: int>,
    "processes": [
        {"name": "Cooking", "emoji": "🍳"},
        {"name": "Cleaning", "emoji": "🧹"},
        ...
    ],
    "style": "embed" | "plain",
    "body_template": "Queue for {process}\nRequested by {username} in {channel}",
    "embed_title": "Queue: {process}",        # only used when style == "embed"
    "use_user_avatar": true,                  # DEPRECATED - kept for backwards compat
    "use_server_avatar": false,               # DEPRECATED - kept for backwards compat
    "embed_color": "5865F2",                  # hex string, no '#'
    "embed_author_name": "{user_name}",
    "embed_author_icon": "{useravatar}",      # "{useravatar}" | "{serverravatar}" | raw URL | ""
    "embed_footer_text": "Queue System",
    "embed_footer_icon": "{serverravatar}",   # same token rules as author icon
    "embed_image": "{useravatar}",            # same token rules
    "embed_thumbnail": "",                    # same token rules
    "attach_plain_message": true,             # if true, sends content text alongside the embed
    "plain_message_template": "{username} you're up next!",
    "interaction_style": "buttons" | "dropdown",
    "queue_channel_id": 123456789,            # set via /queue channel; fixed target for /queue post
    "staff_role_ids": [111111111, 222222222]  # role IDs toggled via /queue staff role; can use /queue post
}
"""

import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient


class Database:
    def __init__(self, uri: str, db_name: str):
        self._client = AsyncIOMotorClient(uri, tlsCAFile=certifi.where())
        self.db = self._client[db_name]
        self.configs = self.db["guild_configs"]

    async def get_config(self, guild_id: int) -> dict | None:
        return await self.configs.find_one({"_id": guild_id})

    async def upsert_config(self, guild_id: int, data: dict) -> None:
        await self.configs.update_one(
            {"_id": guild_id},
            {"$set": data},
            upsert=True,
        )

    async def set_processes(self, guild_id: int, processes: list[dict]) -> None:
        await self.upsert_config(guild_id, {"processes": processes})

    async def set_style(self, guild_id: int, style: str) -> None:
        await self.upsert_config(guild_id, {"style": style})

    async def set_template(self, guild_id: int, **fields) -> None:
        await self.upsert_config(guild_id, fields)

    async def set_queue_channel(self, guild_id: int, channel_id: int) -> None:
        await self.upsert_config(guild_id, {"queue_channel_id": channel_id})


def build_database() -> Database:
    uri = os.environ["MONGO_URI2"]
    db_name = os.getenv("MONGO_DB_NAME", "queue_bot")
    return Database(uri, db_name)
