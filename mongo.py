"""
MongoDB access layer for storing and retrieving custom embeds.

Each embed is stored as a single document, keyed by (guild_id, name), so
different servers can each have their own "okaayyy"-style embeds without
clashing with one another.
"""

import os
import motor.motor_asyncio

MONGO_URI = os.getenv("MONGO_URI1", "mongodb://localhost:27017")

_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
_db = _client["embed_bot"]
embeds_collection = _db["embeds"]
autoresponders_collection = _db["autoresponders"]
stickies_collection = _db["stickies"]


async def ensure_indexes() -> None:
    """Create the indexes we rely on. Call once on startup."""
    await embeds_collection.create_index(
        [("guild_id", 1), ("name", 1)], unique=True
    )
    await autoresponders_collection.create_index(
        [("guild_id", 1), ("trigger", 1)], unique=True
    )
    await stickies_collection.create_index(
        [("guild_id", 1), ("channel_id", 1)], unique=True
    )


async def embed_name_exists(guild_id: int, name: str) -> bool:
    count = await embeds_collection.count_documents(
        {"guild_id": guild_id, "name": name}
    )
    return count > 0


async def save_embed(guild_id: int, name: str, data: dict, author_id: int) -> None:
    payload = dict(data)
    payload["guild_id"] = guild_id
    payload["name"] = name
    payload["author_id"] = author_id
    await embeds_collection.update_one(
        {"guild_id": guild_id, "name": name},
        {"$set": payload},
        upsert=True,
    )


async def get_embed(guild_id: int, name: str) -> dict | None:
    return await embeds_collection.find_one({"guild_id": guild_id, "name": name})


async def delete_embed(guild_id: int, name: str) -> None:
    await embeds_collection.delete_one({"guild_id": guild_id, "name": name})


async def list_embeds(guild_id: int) -> list[dict]:
    cursor = embeds_collection.find({"guild_id": guild_id})
    return [doc async for doc in cursor]


# ---------------------------------------------------------------------------
# Autoresponders
# ---------------------------------------------------------------------------

async def autoresponder_trigger_exists(guild_id: int, trigger: str) -> bool:
    count = await autoresponders_collection.count_documents(
        {"guild_id": guild_id, "trigger": trigger}
    )
    return count > 0


async def save_autoresponder(guild_id: int, trigger: str, data: dict, author_id: int) -> None:
    payload = dict(data)
    payload["guild_id"] = guild_id
    payload["trigger"] = trigger
    payload["author_id"] = author_id
    await autoresponders_collection.update_one(
        {"guild_id": guild_id, "trigger": trigger},
        {"$set": payload},
        upsert=True,
    )


async def get_autoresponder(guild_id: int, trigger: str) -> dict | None:
    return await autoresponders_collection.find_one({"guild_id": guild_id, "trigger": trigger})


async def delete_autoresponder(guild_id: int, trigger: str) -> None:
    await autoresponders_collection.delete_one({"guild_id": guild_id, "trigger": trigger})


async def list_autoresponders(guild_id: int) -> list[dict]:
    cursor = autoresponders_collection.find({"guild_id": guild_id})
    return [doc async for doc in cursor]


# ---------------------------------------------------------------------------
# Sticky messages
# ---------------------------------------------------------------------------

async def sticky_exists(guild_id: int, channel_id: int) -> bool:
    count = await stickies_collection.count_documents(
        {"guild_id": guild_id, "channel_id": channel_id}
    )
    return count > 0


async def save_sticky(guild_id: int, channel_id: int, data: dict, author_id: int) -> None:
    payload = dict(data)
    payload["guild_id"] = guild_id
    payload["channel_id"] = channel_id
    payload["author_id"] = author_id
    await stickies_collection.update_one(
        {"guild_id": guild_id, "channel_id": channel_id},
        {"$set": payload},
        upsert=True,
    )


async def get_sticky(guild_id: int, channel_id: int) -> dict | None:
    return await stickies_collection.find_one({"guild_id": guild_id, "channel_id": channel_id})


async def update_sticky_message_id(guild_id: int, channel_id: int, message_id: int | None) -> None:
    await stickies_collection.update_one(
        {"guild_id": guild_id, "channel_id": channel_id},
        {"$set": {"last_message_id": message_id}},
    )


async def delete_sticky(guild_id: int, channel_id: int) -> None:
    await stickies_collection.delete_one({"guild_id": guild_id, "channel_id": channel_id})


async def list_stickies(guild_id: int) -> list[dict]:
    cursor = stickies_collection.find({"guild_id": guild_id})
    return [doc async for doc in cursor]
