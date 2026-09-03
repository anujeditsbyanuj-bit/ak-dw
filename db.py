import logging
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URI

logger = logging.getLogger("diskwala_bot")

_client = AsyncIOMotorClient(MONGO_URI)
_db = _client["diskwala_bot"]
_file_cache = _db["file_cache"]  # {_id: "<link>|<quality>", file_id, name, size, quality_label}
# schema: {chat_id, banned, premium_lifetime, premium_until, daily_date,
#          daily_count, total_downloads}
_user_stats = _db["user_stats"]
_channels = _db["channels"]  # {_id: chat_id} — owner-linked log/backup channels or groups


def _make_key(link: str, quality: str) -> str:
    return f"{link}|{quality}"


async def get_cached_file(link: str, quality: str) -> dict | None:
    """Return cached video metadata for this (link, quality), or None if not cached."""
    try:
        doc = await _file_cache.find_one({"_id": _make_key(link, quality)})
        return doc
    except Exception as e:
        logger.warning(f"Mongo get_cached_file failed: {e}")
        return None


async def set_cached_file(link: str, quality: str, file_id: str, name: str, size: int, quality_label: str):
    """Store/overwrite cached video metadata for this (link, quality)."""
    try:
        await _file_cache.update_one(
            {"_id": _make_key(link, quality)},
            {
                "$set": {
                    "file_id": file_id,
                    "name": name,
                    "size": size,
                    "quality_label": quality_label,
                }
            },
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"Mongo set_cached_file failed: {e}")


async def delete_cached_file(link: str, quality: str):
    """Remove a stale cache entry (e.g. when the cached file_id no longer works)."""
    try:
        await _file_cache.delete_one({"_id": _make_key(link, quality)})
    except Exception as e:
        logger.warning(f"Mongo delete_cached_file failed: {e}")


# ---------------------------------------------------------------------
# Premium plans / user stats
# ---------------------------------------------------------------------

async def ensure_indexes():
    try:
        await _user_stats.create_index("chat_id", unique=True)
    except Exception as e:
        logger.warning(f"Mongo user_stats index setup failed: {e}")


async def is_banned(chat_id: int) -> bool:
    try:
        rec = await _user_stats.find_one({"chat_id": chat_id})
    except Exception as e:
        logger.warning(f"Mongo ban lookup failed: {e}")
        return False
    return bool(rec and rec.get("banned"))


async def set_banned(chat_id: int, banned: bool):
    await _user_stats.update_one(
        {"chat_id": chat_id}, {"$set": {"banned": banned}}, upsert=True
    )


async def get_premium_status(chat_id: int) -> dict:
    """Returns {'is_premium': bool, 'lifetime': bool, 'expires_at': datetime|None}."""
    try:
        rec = await _user_stats.find_one({"chat_id": chat_id})
    except Exception as e:
        logger.warning(f"Mongo premium lookup failed: {e}")
        rec = None

    if not rec:
        return {"is_premium": False, "lifetime": False, "expires_at": None}

    if rec.get("premium_lifetime"):
        return {"is_premium": True, "lifetime": True, "expires_at": None}

    expires_at = rec.get("premium_until")
    if expires_at and expires_at > datetime.utcnow():
        return {"is_premium": True, "lifetime": False, "expires_at": expires_at}

    return {"is_premium": False, "lifetime": False, "expires_at": None}


async def set_premium(chat_id: int, days: int | None):
    """days=None means lifetime. Extends from 'now', not stacked on top of
    any existing remaining time."""
    if days is None:
        await _user_stats.update_one(
            {"chat_id": chat_id},
            {"$set": {"premium_lifetime": True}, "$unset": {"premium_until": ""}},
            upsert=True,
        )
    else:
        expires_at = datetime.utcnow() + timedelta(days=days)
        await _user_stats.update_one(
            {"chat_id": chat_id},
            {"$set": {"premium_until": expires_at}, "$unset": {"premium_lifetime": ""}},
            upsert=True,
        )


async def remove_premium(chat_id: int):
    await _user_stats.update_one(
        {"chat_id": chat_id},
        {"$unset": {"premium_until": "", "premium_lifetime": ""}},
        upsert=True,
    )


async def get_daily_count(chat_id: int) -> int:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        rec = await _user_stats.find_one({"chat_id": chat_id})
    except Exception as e:
        logger.warning(f"Mongo daily-limit lookup failed: {e}")
        return 0
    if rec and rec.get("daily_date") == today:
        return rec.get("daily_count", 0)
    return 0


async def bump_daily_count(chat_id: int):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        # Roll over to today if stale, then atomically increment (two
        # separate updates so concurrent finishes can't stomp each other).
        await _user_stats.update_one(
            {"chat_id": chat_id, "daily_date": {"$ne": today}},
            {"$set": {"daily_date": today, "daily_count": 0}},
            upsert=True,
        )
        await _user_stats.update_one(
            {"chat_id": chat_id, "daily_date": today},
            {"$inc": {"daily_count": 1}},
        )
    except Exception as e:
        logger.warning(f"Mongo daily-limit update failed: {e}")


async def bump_total_downloads(chat_id: int):
    try:
        await _user_stats.update_one(
            {"chat_id": chat_id}, {"$inc": {"total_downloads": 1}}, upsert=True
        )
    except Exception as e:
        logger.warning(f"Mongo total_downloads update failed: {e}")


async def get_total_users() -> int:
    try:
        return await _user_stats.count_documents({})
    except Exception as e:
        logger.warning(f"Mongo user-count failed: {e}")
        return 0


async def get_stats_summary() -> dict:
    try:
        total_users = await _user_stats.count_documents({})
        banned_count = await _user_stats.count_documents({"banned": True})
        premium_count = await _user_stats.count_documents({
            "$or": [
                {"premium_lifetime": True},
                {"premium_until": {"$gt": datetime.utcnow()}},
            ]
        })
        pipeline = [{"$group": {"_id": None, "total": {"$sum": "$total_downloads"}}}]
        agg = await _user_stats.aggregate(pipeline).to_list(length=1)
        total_downloads = agg[0]["total"] if agg else 0
        total_files_cached = await _file_cache.count_documents({})
        return {
            "total_users": total_users,
            "banned_count": banned_count,
            "premium_count": premium_count,
            "total_downloads": total_downloads,
            "total_files_cached": total_files_cached,
        }
    except Exception as e:
        logger.warning(f"Mongo stats query failed: {e}")
        return {}


async def all_chat_ids() -> list[int]:
    try:
        return [doc["chat_id"] async for doc in _user_stats.find({}, {"chat_id": 1})]
    except Exception as e:
        logger.warning(f"Mongo chat_id list failed: {e}")
        return []


# ---------------------------------------------------------------------
# Linked channels/groups — owner can link one or more, and every
# successfully downloaded video gets copied there as a backup log.
# ---------------------------------------------------------------------

async def add_channel(chat_id: int):
    await _channels.update_one({"_id": chat_id}, {"$set": {"_id": chat_id}}, upsert=True)


async def remove_channel(chat_id: int) -> bool:
    result = await _channels.delete_one({"_id": chat_id})
    return result.deleted_count > 0


async def remove_all_channels() -> int:
    result = await _channels.delete_many({})
    return result.deleted_count


async def get_channels() -> list[int]:
    try:
        return [doc["_id"] async for doc in _channels.find({})]
    except Exception as e:
        logger.warning(f"Mongo channels list failed: {e}")
        return []


async def register_user_if_new(chat_id: int) -> bool:
    """Ensures a user_stats doc exists for this chat_id. Returns True the
    first time this chat_id is ever seen (for new-user log notifications)."""
    try:
        result = await _user_stats.update_one(
            {"chat_id": chat_id},
            {"$setOnInsert": {"chat_id": chat_id}},
            upsert=True,
        )
        return result.upserted_id is not None
    except Exception as e:
        logger.warning(f"Mongo register_user_if_new failed: {e}")
        return False
