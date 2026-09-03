# Diskwala Bot

Telegram bot that downloads and streams videos from Diskwala links.

## Features

- Download Diskwala/Flezen videos directly to Telegram
- Get streamable links for in-app viewing
- Multi-link support (send multiple links at once)
- Progress tracking with speed display
- Premium plans with daily free-download limit
- Auto-delete sent files after a configurable time
- Admin tools: grant/revoke premium, ban/unban, stats, broadcast

## Prerequisites

- Python 3.10+
- A Telegram bot token (from @BotFather)
- Telegram API credentials (from my.telegram.org)

## Setup

### 1. Get Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the instructions
3. Copy the bot token

### 2. Get Telegram API Credentials

1. Go to [my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Go to "API development tools"
4. Create an app and copy `api_id` and `api_hash`

### 3. Generate Telethon Session String

Create a file called `gen_session.py`:

```python
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = 12345678          # your api_id
api_hash = "your_api_hash"

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print(client.session.save())
```

Run it: `python gen_session.py`

Log in when prompted. Copy the printed session string.

**IMPORTANT:** Keep this session string secret - it's like a password to your Telegram account.

### 4. Install and Run

```bash
cd diskwala_bot
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your credentials
python main.py
```

### 5. Set Environment Variables

Edit `.env` file:

```
API_ID=your_api_id
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
SESSION=your_session_string
OWNER_ID=your_telegram_user_id
MONGO_URI=your_mongodb_connection_string
```

## Usage

1. Start a chat with your bot on Telegram
2. Send a Diskwala link (e.g., `https://www.diskwala.com/app/xxxxx`)
3. Choose: Download File or Get Stream Link

## Commands

- `/start` - Welcome message
- `/help` - Show usage instructions
- `/myplan` - Show your plan / status (same as "📊 My Status" button)

### Admin-only (requires being in `ADMINS`)

- `/addpremium <user_id> <days|lifetime>` - Grant premium
- `/removepremium <user_id>` - Revoke premium
- `/ban <user_id>` / `/unban <user_id>` - Ban / unban a user
- `/stats` - Bot usage stats
- `/broadcast <message>` (or reply to a message with `/broadcast`) - Message all known users
- `/set_channel_id <-100xxxxxxxxxx>` - Link a channel/group as a backup log (bot must be admin there)
- `/channel_id` - List all linked channels/groups
- `/del_channel_id [id]` - Unlink one channel (or all, if no id given)

## Linked Backup Channels

Admins can link one or more channels/groups with `/set_channel_id`. Every
successfully downloaded video is then also copied there as a backup —
useful if you want a persistent archive outside the auto-delete window.
Get a channel's ID by forwarding any message from it to @MissRose_bot;
the bot must be an admin in that channel/group.

## Log Channel

Set `LOG_CHANNEL` to a single channel/group ID (bot must be admin there)
to receive event logs:

- Bot startup message
- A notification the first time each new user sends `/start`
- One line per successful download (user + link)

This is separate from the `/set_channel_id` backup channels above, which
store copies of the actual video files rather than text logs.

```
LOG_CHANNEL=-1001234567890   # 0 or unset disables logging
```

## Premium Plans

Free users get `DAILY_FREE_LIMIT` downloads/day (default 10, resets at UTC
midnight). The "💎 Plans" menu button shows pricing tiers and lets a user
tap a plan; there's no payment gateway wired up, so this just tells them
to contact the admin, who then runs `/addpremium <user_id> <days|lifetime>`.

New env vars:

```
ADMINS=123456789,987654321   # comma-separated Telegram user IDs, in addition to OWNER_ID
DAILY_FREE_LIMIT=10           # free downloads/day per user
AUTO_DELETE_SECONDS=3600      # seconds before a sent file auto-deletes (0 = disabled)
```

## Auto-Delete

Every video sent to a user is automatically deleted from that chat after
`AUTO_DELETE_SECONDS` (default 1 hour) and replaced with a notice telling
them to re-download or forward it elsewhere to keep it. The caption on
every sent file also states this up front.

## File Structure

```
diskwala_bot/
├── main.py           # Telegram bot handlers, plans, admin commands, auto-delete
├── diskwala.py       # Diskwala API extraction logic
├── db.py             # MongoDB: file cache, premium/ban/daily-limit tracking
├── config.py         # Configuration (env vars)
├── requirements.txt  # Python dependencies
├── render.yaml        # Render.com deploy config
├── Dockerfile         # Container build
├── .env.example       # Environment template
└── README.md          # This file
```

## Notes

- The bot requires a Telethon user session to authenticate with Diskwala's API
- Session string is equivalent to your Telegram login - keep it safe
- Videos are temporarily downloaded then deleted after sending
