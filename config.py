import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID", "37476811"))
API_HASH = os.getenv("API_HASH", "7aa60670b871050820086c6267371ee6")
BOT_TOKEN = os.getenv("BOT_TOKEN", "7512964694:AAHmnaJ7FBPUOw5l2AvmHodczsjmutUui8Y")
SESSION = os.getenv("SESSION", "1BVtsOJ0Bu7iFhYaPaDWTPDdVAtu310L3iOh4PlTSxrTSyGaJPYQzf6rDgAMM9xGktQy9DodpC5TCCDTBMV3AiS4f5SNUbDR6kiPQ0PHUfj--XOQv82ZW2w2e7SM6GXvGdVTDXczbTBypUSYN0pSu-IMCd5atImWZBG6DvOg8o95pKmC9nc0H5jRMCfTBrtFbB0ba6iysaBd515MDP8fEWefKnRB8k8az61yt3hYhNhG-LQ2xgb1bz845tfZUK2KuKzXnmRZiMUmB_0agXDwQSEnTlJa5NlJxken911hhODilu2VCTKwbnweqj9QFHPDGoJcRZ-1GaVeGI-o1K70J3GZqLdcA_Ls=")
OWNER_ID = int(os.getenv("OWNER_ID", "8730393744"))
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://Anujedit:Anujedit@cluster0.7cs2nhd.mongodb.net/?appName=Cluster0")

TG_BOT_WORKERS = int(os.getenv("TG_BOT_WORKERS", "10"))
DOWNLOAD_DIR = "downloads"
MAX_CONCURRENT_DOWNLOADS = 5

# --- Premium plans ---
# Comma separated list of admin Telegram user IDs (in addition to OWNER_ID)
# who can run /addpremium, /removepremium, /ban, /unban, /stats.
ADMINS = list({OWNER_ID, *[int(x) for x in os.getenv("ADMINS", "8730393744").split(",") if x.strip()]})

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
LOG_CHANNEL = int(os.getenv("LOG_CHANNEL", "-1003824246703"))
