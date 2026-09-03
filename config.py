import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ["API_ID", ""])
API_HASH = os.environ["API_HASH", ""]
BOT_TOKEN = os.environ["BOT_TOKEN", ""]
SESSION = os.environ["SESSION", ""]
OWNER_ID = int(os.environ["OWNER_ID", ""])
MONGO_URI = os.environ["MONGO_URI", ""]

TG_BOT_WORKERS = int(os.getenv("TG_BOT_WORKERS", "4"))
DOWNLOAD_DIR = "downloads"
MAX_CONCURRENT_DOWNLOADS = 5

# --- Premium plans ---
# Comma separated list of admin Telegram user IDs (in addition to OWNER_ID)
# who can run /addpremium, /removepremium, /ban, /unban, /stats.
ADMINS = list({OWNER_ID, *[int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]})

# Free users get this many successful downloads per day (resets at UTC
# midnight); premium users are unlimited.
DAILY_FREE_LIMIT = int(os.getenv("DAILY_FREE_LIMIT", "10"))

# Sent videos/files auto-delete from the chat after this many seconds
# (0 disables auto-delete).
AUTO_DELETE_SECONDS = int(os.getenv("AUTO_DELETE_SECONDS", str(60 * 60)))  # 1 hour

# Single channel/group (bot must be admin there) that receives bot event
# logs: startup message, new-user notifications, and a line per download.
# Leave unset / 0 to disable. This is separate from the per-owner
# /set_channel_id backup channels, which store copies of the actual files.
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "")
