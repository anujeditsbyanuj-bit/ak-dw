import os
import asyncio
import logging
import signal
import time
import uuid
import requests
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from pyrogram.enums import ParseMode, ChatMemberStatus

from config import (
    API_ID, API_HASH, BOT_TOKEN, SESSION, OWNER_ID, TG_BOT_WORKERS,
    DOWNLOAD_DIR, MAX_CONCURRENT_DOWNLOADS, ADMINS, DAILY_FREE_LIMIT,
    AUTO_DELETE_SECONDS, LOG_CHANNEL,
)
from diskwala import fetch_diskwala_video, extract_diskwala_links
from db import (
    get_cached_file, set_cached_file, delete_cached_file, ensure_indexes,
    is_banned, set_banned, get_premium_status, set_premium, remove_premium,
    get_daily_count, bump_daily_count, bump_total_downloads, get_stats_summary,
    all_chat_ids, add_channel, remove_channel, remove_all_channels, get_channels,
    register_user_if_new,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("diskwala_bot")

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Client(
    "diskwala_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workers=TG_BOT_WORKERS,
)

download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

_auth_cache = {"token": None, "expires": 0}

# Maps a short id -> Diskwala link, so callback_data stays under Telegram's 64-byte limit
LINK_CACHE = {}


async def log_event(client: Client, text: str):
    """Best-effort send to LOG_CHANNEL. No-op if unset, never raises."""
    if not LOG_CHANNEL:
        return
    try:
        await client.send_message(LOG_CHANNEL, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"Log-channel send failed: {e}")

# ---------------------------------------------------------------------
# Auto-delete — every file sent to a user gets removed after
# AUTO_DELETE_SECONDS and replaced with a notice.
# ---------------------------------------------------------------------
DELETE_NOTICE_PHOTO = "https://iili.io/CyoYhNI.jpg"
DELETE_NOTICE_TEXT = (
    "Your video / file has been deleted due to restriction.\n\n"
    "if you want to see it again please re download and save.\n\n"
    "आपका विडियो / फाइल डिलीट कर दी गयी है आपको फिर से देखनी है तो फिर से डाउनलोड कर सकते है धन्यवाद!"
)


async def schedule_delete(client: Client, chat_id: int, message_id: int):
    if AUTO_DELETE_SECONDS <= 0:
        return
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    try:
        await client.delete_messages(chat_id, message_id)
    except Exception as e:
        # Message may already be gone (user deleted it, chat cleared, etc.)
        logger.warning(f"Couldn't delete expired file message {message_id} in {chat_id}: {e}")
        return

    try:
        await asyncio.wait_for(
            client.send_photo(
                chat_id=chat_id,
                photo=DELETE_NOTICE_PHOTO,
                caption=DELETE_NOTICE_TEXT,
                parse_mode=ParseMode.HTML,
            ),
            timeout=10,
        )
    except Exception as e:
        logger.warning(f"Couldn't send delete-notice photo to {chat_id}, falling back to text: {e}")
        try:
            await client.send_message(chat_id, DELETE_NOTICE_TEXT, parse_mode=ParseMode.HTML)
        except Exception:
            pass

QUALITY_OPTIONS = [
    [("🎬 2160p", "2160"), ("🎬 1080p", "1080")],
    [("🎬 720p", "720"), ("🎬 480p", "480")],
    [("🎬 240p", "240")],
]


def human_size(n: float) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} PB"


def human_speed(n: float) -> str:
    for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB/s"


async def get_auth_token() -> str:
    if _auth_cache["token"] and time.time() < _auth_cache["expires"]:
        return _auth_cache["token"]

    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.functions.messages import RequestAppWebViewRequest
    from telethon.tl.types import InputBotAppShortName, InputPeerSelf, DataJSON
    from urllib.parse import urlparse, unquote

    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    try:
        await client.connect()
        bot = await client.get_input_entity("sky577bot")
        r = await client(RequestAppWebViewRequest(
            peer=InputPeerSelf(),
            app=InputBotAppShortName(bot_id=bot, short_name="open"),
            platform="android",
            write_allowed=True,
            start_param="",
            theme_params=DataJSON("{}"),
        ))
        token = unquote(urlparse(r.url).fragment.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion=", 1)[0])
        _auth_cache["token"] = token
        _auth_cache["expires"] = time.time() + 1800
        return token
    finally:
        await client.disconnect()


START_PHOTO_URL = "https://iili.io/nH8JrFI.jpg"

MAIN_MENU_KB = ReplyKeyboardMarkup(
    [
        ["💎 Plans", "📊 My Status"],
        ["❓ Help", "☎️ Support"],
    ],
    resize_keyboard=True,
)

MENU_BUTTON_TEXTS = {"💎 Plans", "📊 My Status", "❓ Help", "☎️ Support"}

# ---------------------------------------------------------------------
# Premium plans — informational only, there's no payment gateway wired
# up here. A plan tap tells the user how to contact the admin to get it
# activated manually via /addpremium.
# ---------------------------------------------------------------------
PLANS = [
    (19, "12 Days"),
    (29, "21 Days"),
    (45, "35 Days"),
    (99, "99 Days"),
    (999, "Lifetime Access"),
]

PLANS_TEXT = (
    "💎 <b>Premium Plans</b>\n\n"
    "Apne budget ke hisaab se plan chuno:\n\n"
    "• ₹19 → 12 Days\n"
    "• ₹29 → 21 Days\n"
    "• ₹45 → 35 Days\n"
    "• ₹99 → 99 Days\n"
    "• ₹999 → Lifetime Access ♾️\n\n"
    "👇 Plan pe tap karo — shuru ho jao!"
)


def plans_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for price, label in PLANS:
        tag = " ♾️" if price == 999 else ""
        rows.append([InlineKeyboardButton(f"🔵 ₹{price} - {label}{tag}", callback_data=f"plan_{price}")])
    return InlineKeyboardMarkup(rows)


def status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("💎 View Plans", callback_data="show_plans")]])


@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, m: Message):
    display_name = smallcaps(m.from_user.first_name or "there")
    caption = (
        f"👋 ʜᴇʏ ᴛʜᴇʀᴇ, {display_name}!\n\n"
        "⚡ ɪ'ᴍ ᴀ ᴠᴇʀʏ ᴘᴏᴡᴇʀꜰᴜʟ ᴅɪꜱᴋᴡᴀʟᴀ ᴅᴏᴡɴʟᴏᴀᴅᴇʀ ʙᴏᴛ.\n\n"
        "📥 ꜱɪᴍᴘʟʏ ꜱᴇɴᴅ ᴍᴇ ᴀɴʏ ᴅɪꜱᴋᴡᴀʟᴀ ᴜʀʟ, ᴀɴᴅ ɪ'ʟʟ ꜰᴇᴛᴄʜ ᴛʜᴇ ᴅɪʀᴇᴄᴛ ᴠɪᴅᴇᴏ ꜰᴏʀ ʏᴏᴜ ɪɴ ꜱᴇᴄᴏɴᴅꜱ.\n\n"
        "🚀 ᴜʟᴛʀᴀ-ꜰᴀꜱᴛ ᴘʀᴏᴄᴇꜱꜱɪɴɢ\n"
        "🎬 ɪɴꜱᴛᴀɴᴛ ᴠɪᴅᴇᴏ ᴇxᴛʀᴀᴄᴛɪᴏɴ\n"
        "⚡ ʟɪɢʜᴛɴɪɴɢ-ꜱᴘᴇᴇᴅ ᴅᴏᴡɴʟᴏᴀᴅꜱ\n"
        "🛡️ ʀᴇʟɪᴀʙʟᴇ & ꜱᴛᴀʙʟᴇ ꜱᴇʀᴠɪᴄᴇ\n"
        "💎 ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴꜱ ᴀᴠᴀɪʟᴀʙʟᴇ\n"
        "🔗 ᴊᴜꜱᴛ ᴘᴀꜱᴛᴇ ʏᴏᴜʀ ᴅɪꜱᴋᴡᴀʟᴀ ʟɪɴᴋ ʙᴇʟᴏᴡ ᴀɴᴅ ʟᴇᴛ ᴛʜᴇ ᴍᴀɢɪᴄ ʙᴇɢɪɴ!\n\n"
        "━━━━━━━━━━━━━━━ \n"
        "👑 ᴘᴏᴡᴇʀᴇᴅ ʙʏ ᴀɴᴜᴊ ᴋᴜᴍᴀʀ\n"
        "⚡ ꜱᴘᴇᴇᴅ • ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ • ʀᴇʟɪᴀʙɪʟɪᴛʏ\n"
        "━━━━━━━━━━━━━━━"
    )
    try:
        await m.reply_photo(START_PHOTO_URL, caption=caption, reply_markup=MAIN_MENU_KB)
    except Exception as e:
        logger.warning(f"start photo failed, falling back to text: {e}")
        await m.reply(caption, reply_markup=MAIN_MENU_KB)

    is_new = await register_user_if_new(m.from_user.id)
    if is_new:
        uname = f"@{m.from_user.username}" if m.from_user.username else "(no username)"
        await log_event(
            client,
            "🆕 <b>New User</b>\n\n"
            f"👤 Name: {m.from_user.first_name}\n"
            f"🔗 Username: {uname}\n"
            f"🆔 ID: <code>{m.from_user.id}</code>",
        )


@app.on_message(filters.command("help") & filters.private)
async def help_handler(client: Client, m: Message):
    await m.reply(
        "<b>How to use:</b>\n\n"
        "1. Copy a Diskwala link\n"
        "2. Send it here\n"
        "3. Choose: Download or Stream",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.private & filters.text & filters.regex(r"^💎 Plans$"))
async def plans_menu_handler(client: Client, m: Message):
    await m.reply(PLANS_TEXT, reply_markup=plans_keyboard(), parse_mode=ParseMode.HTML)


@app.on_message(filters.private & filters.text & filters.regex(r"^📊 My Status$"))
async def status_menu_handler(client: Client, m: Message):
    await show_my_status(client, m)


@app.on_message(filters.command("myplan") & filters.private)
async def myplan_cmd(client: Client, m: Message):
    await show_my_status(client, m)


async def show_my_status(client: Client, m: Message):
    chat_id = m.from_user.id
    premium = await get_premium_status(chat_id)
    if premium["lifetime"]:
        type_line = "💎 Premium (Lifetime ♾️)"
    elif premium["is_premium"]:
        days_left = (premium["expires_at"] - datetime.utcnow()).days + 1
        type_line = f"💎 Premium ({days_left} day{'s' if days_left != 1 else ''} left)"
    else:
        type_line = "Free"

    text = (
        "<b>📊 Your Status</b>\n\n"
        f"User ID: <code>{chat_id}</code>\n"
        f"Plan: <code>{type_line}</code>\n"
    )
    if not premium["is_premium"]:
        used_today = await get_daily_count(chat_id)
        remaining = max(0, DAILY_FREE_LIMIT - used_today)
        text += f"Today's downloads: {used_today}/{DAILY_FREE_LIMIT} ({remaining} left)\n\n"
        text += "💎 Premium lo — unlimited downloads ka maza lo!"
        await m.reply(text, reply_markup=status_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await m.reply(text, parse_mode=ParseMode.HTML)


@app.on_callback_query(filters.regex(r"^show_plans$"))
async def show_plans_cb(client: Client, query):
    await query.message.reply(PLANS_TEXT, reply_markup=plans_keyboard(), parse_mode=ParseMode.HTML)
    await query.answer()


@app.on_callback_query(filters.regex(r"^plan_\d+$"))
async def plan_selected_cb(client: Client, query):
    price = query.data.split("_", 1)[1]
    await query.message.reply(
        f"💎 Aapne <b>₹{price}</b> plan select kiya.\n\n"
        f"Isko activate karwane ke liye support se contact karo, plan name ke saath.",
        parse_mode=ParseMode.HTML,
    )
    await query.answer()


# ---------------------------------------------------------------------
# Admin-only premium/ban management
# ---------------------------------------------------------------------

@app.on_message(filters.command("addpremium") & filters.private & filters.user(ADMINS))
async def addpremium_cmd(client: Client, m: Message):
    args = m.command[1:]
    if len(args) < 2:
        return await m.reply(
            "⚠️ <b>Usage:</b> <code>/addpremium &lt;user_id&gt; &lt;days|lifetime&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
    try:
        target_id = int(args[0])
    except ValueError:
        return await m.reply("⚠️ user_id must be a number.")

    if args[1].lower() == "lifetime":
        await set_premium(target_id, None)
        note = "Lifetime ♾️"
    else:
        try:
            days = int(args[1])
        except ValueError:
            return await m.reply("⚠️ days must be a number, or 'lifetime'.")
        if days < 1:
            return await m.reply("⚠️ days must be at least 1.")
        await set_premium(target_id, days)
        note = f"{days} day{'s' if days != 1 else ''}"

    await m.reply(f"✅ Premium granted to <code>{target_id}</code> — {note}.", parse_mode=ParseMode.HTML)
    try:
        await client.send_message(target_id, f"🎉 You've been given Premium ({note}) by the admin!")
    except Exception as e:
        logger.warning(f"Couldn't notify {target_id} about premium grant: {e}")


@app.on_message(filters.command("removepremium") & filters.private & filters.user(ADMINS))
async def removepremium_cmd(client: Client, m: Message):
    args = m.command[1:]
    if len(args) < 1:
        return await m.reply(
            "⚠️ <b>Usage:</b> <code>/removepremium &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
    try:
        target_id = int(args[0])
    except ValueError:
        return await m.reply("⚠️ user_id must be a number.")

    await remove_premium(target_id)
    await m.reply(f"✅ Premium removed for <code>{target_id}</code>.", parse_mode=ParseMode.HTML)


@app.on_message(filters.command("ban") & filters.private & filters.user(ADMINS))
async def ban_cmd(client: Client, m: Message):
    args = m.command[1:]
    if len(args) < 1:
        return await m.reply("⚠️ <b>Usage:</b> <code>/ban &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
    try:
        target_id = int(args[0])
    except ValueError:
        return await m.reply("⚠️ user_id must be a number.")
    if target_id in ADMINS:
        return await m.reply("⚠️ Can't ban an admin.")

    await set_banned(target_id, True)
    await m.reply(f"🚫 <code>{target_id}</code> has been banned.", parse_mode=ParseMode.HTML)
    try:
        await client.send_message(target_id, "🚫 You've been banned from using this bot.")
    except Exception as e:
        logger.warning(f"Couldn't notify {target_id} about ban: {e}")


@app.on_message(filters.command("unban") & filters.private & filters.user(ADMINS))
async def unban_cmd(client: Client, m: Message):
    args = m.command[1:]
    if len(args) < 1:
        return await m.reply("⚠️ <b>Usage:</b> <code>/unban &lt;user_id&gt;</code>", parse_mode=ParseMode.HTML)
    try:
        target_id = int(args[0])
    except ValueError:
        return await m.reply("⚠️ user_id must be a number.")

    await set_banned(target_id, False)
    await m.reply(f"✅ <code>{target_id}</code> has been unbanned.", parse_mode=ParseMode.HTML)
    try:
        await client.send_message(target_id, "✅ You've been unbanned — you can use the bot again.")
    except Exception as e:
        logger.warning(f"Couldn't notify {target_id} about unban: {e}")


@app.on_message(filters.command("stats") & filters.private & filters.user(ADMINS))
async def stats_cmd(client: Client, m: Message):
    s = await get_stats_summary()
    if not s:
        return await m.reply("❌ Couldn't fetch stats.")
    text = (
        "📊 <b>Bot Stats</b>\n\n"
        f"👥 <b>Total Users:</b> {s['total_users']}\n"
        f"💎 <b>Premium Users:</b> {s['premium_count']}\n"
        f"🚫 <b>Banned Users:</b> {s['banned_count']}\n\n"
        f"📦 <b>Total Downloads:</b> {s['total_downloads']}\n"
        f"🗂️ <b>Unique Files Cached:</b> {s['total_files_cached']}"
    )
    await m.reply(text, parse_mode=ParseMode.HTML)


@app.on_message(filters.command("broadcast") & filters.private & filters.user(ADMINS))
async def broadcast_cmd(client: Client, m: Message):
    reply = m.reply_to_message
    broadcast_text = None
    if len(m.command) >= 2:
        broadcast_text = m.text.split(None, 1)[1]
    elif not reply:
        return await m.reply(
            "⚠️ <b>Usage:</b> <code>/broadcast &lt;message&gt;</code>\n"
            "(or reply to a message with just <code>/broadcast</code> to forward that)",
            parse_mode=ParseMode.HTML,
        )

    chat_ids = await all_chat_ids()
    status_msg = await m.reply(f"📣 Broadcasting to {len(chat_ids)} users...")
    sent, failed = 0, 0
    for cid in chat_ids:
        try:
            if broadcast_text is not None:
                await client.send_message(cid, broadcast_text)
            else:
                await client.copy_message(chat_id=cid, from_chat_id=m.chat.id, message_id=reply.id)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"📣 <b>Broadcast done.</b>\n\n✅ Sent: {sent}\n❌ Failed: {failed}", parse_mode=ParseMode.HTML
    )


# ---------------------------------------------------------------------
# Linked channels/groups — every successfully downloaded video also gets
# copied to each linked channel as a backup log. Bot must be an admin
# in the target channel/group for this to work.
# ---------------------------------------------------------------------

@app.on_message(filters.command("set_channel_id") & filters.private & filters.user(ADMINS))
async def set_channel_id_cmd(client: Client, m: Message):
    args = m.command[1:]
    if len(args) < 1:
        return await m.reply(
            "⚠️ <b>Usage:</b> <code>/set_channel_id -100xxxxxxxxxx</code>\n"
            "<b>Example:</b> <code>/set_channel_id -1001234567890</code>\n\n"
            "<b>Note:</b> The ID must start with -100 and you must make me an admin "
            "in that channel/group. Get your Channel ID by forwarding any message "
            "from the channel to @MissRose_bot.\n\n"
            "You can link more than one channel/group — just run this command again "
            "with a different ID. Use /channel_id to see everything you've linked, and "
            "/del_channel_id <id> to unlink one (or /del_channel_id with no id to unlink everything).",
            parse_mode=ParseMode.HTML,
        )

    raw = args[0]
    if not raw.startswith("-100") or not raw.lstrip("-").isdigit():
        return await m.reply("⚠️ The ID must start with <code>-100</code>, e.g. <code>-1001234567890</code>.", parse_mode=ParseMode.HTML)

    chat_id = int(raw)
    try:
        chat = await client.get_chat(chat_id)
        member = await client.get_chat_member(chat_id, "me")
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            return await m.reply("⚠️ I'm in that chat but I'm not an admin there. Please promote me first.")
    except Exception as e:
        return await m.reply(
            f"⚠️ Couldn't verify that chat — make sure I'm added as admin there first.\n<code>{str(e)[:300]}</code>",
            parse_mode=ParseMode.HTML,
        )

    await add_channel(chat_id)
    title = getattr(chat, "title", None) or str(chat_id)
    await m.reply(f"✅ Linked <b>{title}</b> (<code>{chat_id}</code>).", parse_mode=ParseMode.HTML)


NO_CUSTOM_CHANNEL_TEXT = (
    "❌ ɴᴏ ᴄᴜsᴛᴏᴍ ᴄʜᴀɴɴᴇʟ sᴇᴛ!\n\n"
    "You don't have any custom channel/group configured.\n"
    "To set one: /set_channel_id"
)


@app.on_message(filters.command("channel_id") & filters.private & filters.user(ADMINS))
async def channel_id_cmd(client: Client, m: Message):
    ids = await get_channels()
    if not ids:
        return await m.reply(NO_CUSTOM_CHANNEL_TEXT)

    lines = []
    for cid in ids:
        try:
            chat = await client.get_chat(cid)
            title = getattr(chat, "title", None) or str(cid)
        except Exception:
            title = "(unreachable)"
        lines.append(f"• <b>{title}</b> — <code>{cid}</code>")

    await m.reply("🔗 <b>Linked Channels/Groups</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


@app.on_message(filters.command("del_channel_id") & filters.private & filters.user(ADMINS))
async def del_channel_id_cmd(client: Client, m: Message):
    ids = await get_channels()
    if not ids:
        return await m.reply(NO_CUSTOM_CHANNEL_TEXT)

    args = m.command[1:]
    if not args:
        count = await remove_all_channels()
        return await m.reply(f"🗑️ Unlinked all channels/groups ({count} removed).")

    try:
        chat_id = int(args[0])
    except ValueError:
        return await m.reply("⚠️ Channel ID must be a number, e.g. <code>-1001234567890</code>.", parse_mode=ParseMode.HTML)

    removed = await remove_channel(chat_id)
    if removed:
        await m.reply(f"🗑️ Unlinked <code>{chat_id}</code>.", parse_mode=ParseMode.HTML)
    else:
        await m.reply(f"⚠️ <code>{chat_id}</code> wasn't linked.", parse_mode=ParseMode.HTML)


async def backup_to_linked_channels(client: Client, from_chat_id: int, message_id: int):
    """Best-effort copy of a sent file into every linked channel. Failures
    (bot not admin, channel deleted, etc.) are logged and otherwise ignored —
    this must never break the user-facing download flow."""
    channel_ids = await get_channels()
    for cid in channel_ids:
        try:
            await client.copy_message(chat_id=cid, from_chat_id=from_chat_id, message_id=message_id)
        except Exception as e:
            logger.warning(f"Backup copy to linked channel {cid} failed: {e}")


@app.on_message(filters.private & filters.text & filters.regex(r"^❓ Help$"))
async def help_menu_handler(client: Client, m: Message):
    await help_handler(client, m)


@app.on_message(filters.private & filters.text & filters.regex(r"^☎️ Support$"))
async def support_menu_handler(client: Client, m: Message):
    await m.reply(
        f'<b>☎️ Support</b>\n\nNeed help? Contact <a href="{POWERED_BY_URL}">{POWERED_BY}</a>.',
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.private & ~filters.command(["start", "help"]))
async def link_handler(client: Client, m: Message):
    text = m.text or m.caption or ""
    if text.strip() in MENU_BUTTON_TEXTS:
        return
    links = extract_diskwala_links(text)
    if not links:
        return

    chat_id = m.from_user.id
    if await is_banned(chat_id):
        await m.reply("🚫 You've been banned from using this bot.")
        return

    premium = await get_premium_status(chat_id)
    if not premium["is_premium"]:
        used = await get_daily_count(chat_id)
        if used >= DAILY_FREE_LIMIT:
            await m.reply(
                f"🚫 <b>Daily limit reached</b> ({DAILY_FREE_LIMIT}/day for Free users).\n\n"
                "💎 Get Premium for unlimited downloads — tap below!",
                reply_markup=status_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

    for i, link in enumerate(links):
        tag = f"[{i+1}/{len(links)}]"
        await process_link(client, m, link, tag)


async def process_link(client: Client, m: Message, link: str, tag: str):
    status = await m.reply(
        f"<b>Fetching video info {tag}...</b>\n<code>{link}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        auth = await get_auth_token()
        video_info = fetch_diskwala_video(link, auth)

        name = video_info.get("name", "video.mp4")
        size = video_info.get("size", 0)
        download_url = video_info.get("downloadUrl")

        if not download_url:
            await status.edit_text(f"<b>No download URL found {tag}</b>")
            return

        size_str = human_size(size) if size else "Unknown"
        link_id = uuid.uuid4().hex[:10]
        LINK_CACHE[link_id] = link
        await status.edit_text(
            f"<b>Video found {tag}</b>\n\n"
            f"Name: <code>{name}</code>\n"
            f"Size: <code>{size_str}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Download File", callback_data=f"menu|{link_id}")],
                [InlineKeyboardButton("Stream Link", callback_data=f"stream|{link_id}")],
            ]),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error(f"Error processing {link}: {e}")
        await status.edit_text(
            f"<b>Error {tag}</b>\n<code>{str(e)[:500]}</code>",
            parse_mode=ParseMode.HTML,
        )


def quality_menu_markup(link_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(label, callback_data=f"dlq|{link_id}|{q}") for label, q in row]
        for row in QUALITY_OPTIONS
    ]
    rows.append([InlineKeyboardButton("⚡ Auto (Best)", callback_data=f"dlq|{link_id}|auto")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data=f"cancel|{link_id}")])
    return InlineKeyboardMarkup(rows)


@app.on_callback_query(filters.regex(r"^(menu|dlq|stream|cancel)\|"))
async def callback_handler(client: Client, query):
    parts = query.data.split("|")
    action = parts[0]
    link_id = parts[1]
    link = LINK_CACHE.get(link_id)

    if action == "cancel":
        await query.message.edit_text("<b>Cancelled.</b>", parse_mode=ParseMode.HTML)
        LINK_CACHE.pop(link_id, None)
        return

    if not link:
        await query.answer("Link expired, please resend it.", show_alert=True)
        return

    if action == "menu":
        await query.message.edit_text(
            "<b>Select quality:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=quality_menu_markup(link_id),
        )
    elif action == "dlq":
        quality = parts[2]
        await download_video(client, query, link, quality)
    elif action == "stream":
        await send_stream_link(client, query, link)


def generate_thumbnail(video_path: str, thumb_path: str) -> bool:
    """Extract a frame from the video as a fallback thumbnail using ffmpeg."""
    try:
        import subprocess
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", video_path,
                "-ss", "00:00:01", "-vframes", "1",
                "-vf", "scale=320:-1",
                thumb_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return result.returncode == 0 and os.path.exists(thumb_path)
    except Exception as e:
        logger.warning(f"ffmpeg thumbnail generation failed: {e}")
        return False


def download_thumb(thumb_url: str, thumb_path: str) -> bool:
    """Download a thumbnail image from a URL."""
    try:
        r = requests.get(thumb_url, timeout=30)
        r.raise_for_status()
        with open(thumb_path, "wb") as f:
            f.write(r.content)
        return True
    except Exception as e:
        logger.warning(f"Thumbnail download failed: {e}")
        return False


def get_video_height(video_path: str):
    """Return the source video's height in pixels using ffprobe, or None on failure."""
    try:
        import subprocess, json
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=height", "-of", "json",
                video_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        return int(data["streams"][0]["height"])
    except Exception as e:
        logger.warning(f"ffprobe failed: {e}")
        return None


def transcode_video(src_path: str, dst_path: str, target_height: int) -> bool:
    """Downscale a video to target_height using ffmpeg. Returns True on success."""
    try:
        import subprocess
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", src_path,
                "-vf", f"scale=-2:{target_height}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                dst_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3600,
        )
        return result.returncode == 0 and os.path.exists(dst_path)
    except Exception as e:
        logger.warning(f"ffmpeg transcode failed: {e}")
        return False


def human_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def hms(seconds: float) -> str:
    """Format seconds as H:MM:SS (e.g. 0:06:54), matching the requested caption style."""
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


_SMALLCAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
)


def smallcaps(text: str) -> str:
    """Convert ASCII letters to unicode small-caps, leaving other characters untouched."""
    return text.translate(_SMALLCAPS_MAP)


POWERED_BY = "Anuj Kumar"  # change this to whatever name/credit you want shown
POWERED_BY_URL = "https://t.me/anujedits76"  # change this to the profile/channel you want "Powered by" to link to


def build_caption(name: str, size_bytes: int, dl_seconds: float, ul_seconds: float, user_id: int, source_link: str, quality_label: str = "Auto (Best)") -> str:
    powered_text = smallcaps(POWERED_BY)
    powered_html = f'<a href="{POWERED_BY_URL}">{powered_text}</a>' if POWERED_BY_URL else powered_text

    source_text = smallcaps("Diskwala Link")
    source_html = f'<a href="{source_link}">{source_text}</a>' if source_link else source_text

    auto_delete_note = ""
    if AUTO_DELETE_SECONDS > 0:
        auto_delete_note = (
            f"⚠️ This file will auto-delete from here in {human_time(AUTO_DELETE_SECONDS)}.\n"
            "📤 Please forward it to any other chat to save it permanently.\n"
        )

    return (
        "<blockquote>"
        f"📄 {smallcaps('File Name')}: {smallcaps(name)}\n"
        f"📦 {smallcaps('Size')}: {human_size(size_bytes)}\n"
        f"🎞️ {smallcaps('Quality')}: {smallcaps(quality_label)}\n"
        f"⬇️ {smallcaps('Downloaded in')}: {hms(dl_seconds)} sec\n"
        f"⬆️ {smallcaps('Uploaded in')}: {hms(ul_seconds)} sec\n"
        f"🙋 {smallcaps('Uploaded by')}: {user_id}\n"
        f"🔗 {smallcaps('Source')}: {source_html}\n"
        f"{auto_delete_note}"
        "</blockquote>\n\n"
        f"⚡ {smallcaps('Powered by')} {powered_html}"
    )


def progress_bar(pct: float, width: int = 12) -> str:
    filled = int(width * pct / 100)
    return "▓" * filled + "░" * (width - filled)


class ProgressTracker:
    """Throttled Telegram status-message updater for download/upload progress."""

    def __init__(self, status_msg: Message, label: str, name: str, interval: float = 3.0):
        self.status_msg = status_msg
        self.label = label
        self.name = name
        self.interval = interval
        self.start_time = time.time()
        self.last_edit_time = 0.0

    async def update(self, current: int, total: int):
        now = time.time()
        is_done = total and current >= total
        if not is_done and (now - self.last_edit_time) < self.interval:
            return
        self.last_edit_time = now

        elapsed = now - self.start_time
        speed = current / elapsed if elapsed > 0 else 0
        pct = (current / total * 100) if total else 0
        eta = (total - current) / speed if speed > 0 and total else 0

        try:
            await self.status_msg.edit_text(
                f"<b>{self.label}...</b>\n<code>{self.name}</code>\n\n"
                f"{progress_bar(pct)} {pct:.1f}%\n"
                f"Size: {human_size(current)} / {human_size(total)}\n"
                f"Speed: {human_speed(speed)}\n"
                f"ETA: {human_time(eta)}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass

    def sync_update(self, current: int, total: int):
        """For use inside a non-async loop (requests streaming): schedule the coroutine."""
        try:
            asyncio.get_event_loop().create_task(self.update(current, total))
        except Exception:
            pass


async def send_cached_video(client: Client, query, status_msg, link: str, cached: dict) -> bool:
    """Try to resend a previously uploaded video instantly using its cached file_id.
    Returns True on success, False if the cache entry is stale and needs a fresh download."""
    await status_msg.edit_text("<b>⚡ Found in cache, sending instantly...</b>", parse_mode=ParseMode.HTML)
    try:
        ul_start = time.time()
        sent_msg = await client.send_video(
            query.from_user.id, cached["file_id"],
            caption=build_caption(
                name=cached["name"],
                size_bytes=cached["size"],
                dl_seconds=0,
                ul_seconds=time.time() - ul_start,
                user_id=query.from_user.id,
                source_link=link,
                quality_label=cached["quality_label"],
            ),
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
        )
        await status_msg.delete()
        chat_id = query.from_user.id
        premium = await get_premium_status(chat_id)
        if not premium["is_premium"]:
            await bump_daily_count(chat_id)
        await bump_total_downloads(chat_id)
        asyncio.create_task(schedule_delete(client, chat_id, sent_msg.id))
        asyncio.create_task(backup_to_linked_channels(client, chat_id, sent_msg.id))
        asyncio.create_task(log_event(
            client,
            "📥 <b>Download (cache hit)</b>\n\n"
            f"👤 User: <code>{chat_id}</code>\n"
            f"📄 Name: {cached['name']}\n"
            f"🔗 Link: {link}",
        ))
        return True
    except Exception as e:
        logger.warning(f"Cached file_id send failed, will redownload: {e}")
        return False


async def download_video(client: Client, query, link: str, quality: str = "auto"):
    async with download_semaphore:
        status_msg = query.message

        cached = await get_cached_file(link, quality)
        if cached:
            ok = await send_cached_video(client, query, status_msg, link, cached)
            if ok:
                return
            await delete_cached_file(link, quality)

        await status_msg.edit_text("<b>Starting download...</b>", parse_mode=ParseMode.HTML)
        try:
            auth = await get_auth_token()
            video_info = fetch_diskwala_video(link, auth)

            name = video_info.get("name", "video.mp4")
            download_url = video_info.get("downloadUrl")
            thumb_url = video_info.get("thumb")

            if not download_url:
                await status_msg.edit_text("<b>No download URL found</b>")
                return

            if "." not in name:
                name += ".mp4"
            name = "".join(c for c in name if c.isalnum() or c in " ._-"[:])
            out_path = os.path.join(DOWNLOAD_DIR, name)

            await status_msg.edit_text(f"<b>Downloading...</b>\n<code>{name}</code>", parse_mode=ParseMode.HTML)

            dl_start = time.time()
            r = requests.get(download_url, stream=True, timeout=300, allow_redirects=True)
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            dl_tracker = ProgressTracker(status_msg, "Downloading", name)

            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        await dl_tracker.update(downloaded, total)
            # ensure a final 100% update
            await dl_tracker.update(downloaded, total or downloaded)
            dl_seconds = time.time() - dl_start

            # Transcode to the requested quality (downscale only, never upscale)
            quality_label = "Auto (Best)"
            upload_size = total or downloaded
            if quality != "auto":
                target_height = int(quality)
                await status_msg.edit_text(f"<b>Checking source quality...</b>", parse_mode=ParseMode.HTML)
                src_height = get_video_height(out_path)

                if src_height and target_height >= src_height:
                    quality_label = "Auto (Best)"
                elif src_height is None:
                    quality_label = "Auto (Best)"
                else:
                    await status_msg.edit_text(
                        f"<b>Converting to {quality}p...</b>\n<code>{name}</code>\n\n"
                        f"This may take a while depending on file size.",
                        parse_mode=ParseMode.HTML,
                    )
                    transcoded_path = os.path.join(
                        DOWNLOAD_DIR, f"{quality}p_{name}"
                    )
                    ok = transcode_video(out_path, transcoded_path, target_height)
                    if ok:
                        try:
                            os.remove(out_path)
                        except Exception:
                            pass
                        out_path = transcoded_path
                        name = os.path.basename(out_path)
                        upload_size = os.path.getsize(out_path)
                        quality_label = f"{quality}p"
                    else:
                        await status_msg.edit_text(
                            f"<b>Conversion failed, uploading original quality instead...</b>",
                            parse_mode=ParseMode.HTML,
                        )
                        quality_label = "Auto (Best)"

            # Prepare thumbnail: try API-provided thumb first, else extract from video
            thumb_path = out_path + "_thumb.jpg"
            got_thumb = False
            if thumb_url:
                got_thumb = download_thumb(thumb_url, thumb_path)
            if not got_thumb:
                got_thumb = generate_thumbnail(out_path, thumb_path)

            await status_msg.edit_text("<b>Uploading to Telegram...</b>", parse_mode=ParseMode.HTML)
            ul_tracker = ProgressTracker(status_msg, "Uploading", name)
            ul_start = time.time()
            sent_msg = await client.send_video(
                query.from_user.id, out_path,
                caption=build_caption(
                    name=name,
                    size_bytes=upload_size,
                    dl_seconds=dl_seconds,
                    ul_seconds=time.time() - ul_start,
                    user_id=query.from_user.id,
                    source_link=link,
                    quality_label=quality_label,
                ),
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
                thumb=thumb_path if got_thumb else None,
                progress=ul_tracker.update,
            )
            ul_seconds = time.time() - ul_start

            # Cache the file_id so identical future requests can be sent instantly
            if sent_msg.video:
                await set_cached_file(
                    link, quality,
                    file_id=sent_msg.video.file_id,
                    name=name,
                    size=upload_size,
                    quality_label=quality_label,
                )

            # Update caption now that we know the real upload duration
            try:
                await sent_msg.edit_caption(
                    caption=build_caption(
                        name=name,
                        size_bytes=upload_size,
                        dl_seconds=dl_seconds,
                        ul_seconds=ul_seconds,
                        user_id=query.from_user.id,
                        source_link=link,
                        quality_label=quality_label,
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
            await status_msg.delete()
            try:
                os.remove(out_path)
            except Exception:
                pass
            if got_thumb:
                try:
                    os.remove(thumb_path)
                except Exception:
                    pass

            chat_id = query.from_user.id
            premium = await get_premium_status(chat_id)
            if not premium["is_premium"]:
                await bump_daily_count(chat_id)
            await bump_total_downloads(chat_id)
            asyncio.create_task(schedule_delete(client, chat_id, sent_msg.id))
            asyncio.create_task(backup_to_linked_channels(client, chat_id, sent_msg.id))
            asyncio.create_task(log_event(
                client,
                "📥 <b>Download (fresh)</b>\n\n"
                f"👤 User: <code>{chat_id}</code>\n"
                f"📄 Name: {name}\n"
                f"🔗 Link: {link}",
            ))

        except Exception as e:
            logger.error(f"Download error: {e}")
            await status_msg.edit_text(
                f"<b>Download failed</b>\n<code>{str(e)[:500]}</code>",
                parse_mode=ParseMode.HTML,
            )


async def send_stream_link(client: Client, query, link: str):
    try:
        auth = await get_auth_token()
        video_info = fetch_diskwala_video(link, auth)

        name = video_info.get("name", "video.mp4")
        size = video_info.get("size", 0)
        stream_url = video_info.get("streamUrl") or video_info.get("downloadUrl")

        if not stream_url:
            await query.message.edit_text("<b>No stream URL found</b>")
            return

        size_str = human_size(size) if size else "Unknown"
        await query.message.edit_text(
            f"<b>Stream Link Ready</b>\n\n"
            f"Name: <code>{name}</code>\n"
            f"Size: <code>{size_str}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Open Stream", url=stream_url)],
            ]),
        )
    except Exception as e:
        logger.error(f"Stream error: {e}")
        await query.message.edit_text(
            f"<b>Stream link failed</b>\n<code>{str(e)[:500]}</code>",
            parse_mode=ParseMode.HTML,
        )

if __name__ == "__main__":
    logger.info("Starting Diskwala Bot...")
    asyncio.run(main())
