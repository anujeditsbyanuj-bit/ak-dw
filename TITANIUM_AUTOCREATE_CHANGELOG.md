# Titanium: Managed Bots Auto-Create (ported from src--tera_api/Akbots/titanium.py)

Ports the one piece `titanium.py`'s header explicitly called out as left
out: Bot API 9.6 "Managed Bots" auto-create — a native Telegram "Create
Bot?" dialog that connects a clone bot without copy-pasting a token.

## What was added

**`titanium.py`**
- `_managed_bots_available()` — hasattr-based feature check against
  `pyrogram.raw` for the April-2026 Managed Bots TL constructors. Returns
  False (not a crash) on an older kurigram build.
- `_get_managed_bot_token(bot_id)` — resolves a just-created managed
  bot's real token via the HTTP Bot API's `getManagedBotToken`,
  authenticated with this bot's own `BOT_TOKEN`.
- `titanium_autocreate_cb` (`titanium_autocreate` callback) — sends the
  `https://t.me/newbot/...` deep-link button that triggers Telegram's
  native Create-Bot dialog. Gated by `_managed_bots_available()`; falls
  back to pointing the user at `/addbot` if unavailable.
- `_handle_managed_bot_created` — a `RawUpdateHandler` that catches the
  `messageActionManagedBotCreated` service message Telegram sends once
  the user completes the dialog, fetches the token, verifies it by
  actually starting a throwaway `Client`, then saves + boots it as a
  full Titanium clone exactly like `/addbot` does — same `_get_clone_client`
  path, so it's wired with the same download/stream/cancel handlers.
  Only registered when `_managed_bots_available()` is true.
- Panel (`_panel_buttons`/`_panel_text`) now shows an "🤖 Auto-Create
  Bot" button when the feature is available, and the bot-details view
  (`titanium_view` callback) shows **Source: Managed Bots API** vs
  **Manual (@BotFather)** per connected bot.

**`database.py`**
- `add_titanium_bot()` gained a `source: str = "manual"` parameter,
  stored on each `titanium_bots[]` entry (`"manual"` or `"managed"`).
  Both call sites in `titanium.py` (the existing `/addbot` flow and the
  new auto-create flow) now pass it explicitly.

**`requirements.txt`**
- Added `aiohttp==3.9.5` — needed for the non-blocking async HTTP call
  to `getManagedBotToken` (the existing `requests` import is sync and
  would block the bot's event loop if used inside an `async def` handler).

## Update: managed-bot token revocation ported

`_revoke_managed_bot_token(bot_id)` (Bot API 9.6 `replaceManagedBotToken`
— revokes the current token AND returns a fresh one in the same call) is
now ported from the reference and wired into the two spots that actually
apply to this simpler `titanium.py`:

- **Remove-cleanup** — both the ❌ Remove button (`titanium_remove`
  callback) and `/delbot` now call `_revoke_managed_bot_token` for a
  `source="managed"` bot before it's wiped from storage, so a removed
  clone's token can't keep working elsewhere. Manual (`/addbot`) bots
  skip this — no API access to revoke a @BotFather-issued token.
- **Auto token-rotation** — a new 🔄 Rotate Token button, shown only for
  managed bots in the bot-details view (`titanium_view`), triggers
  `titanium_replace:<username>`: revokes the old token, verifies the new
  one Telegram hands back by starting a throwaway `Client`, swaps the
  stored `titanium_bots[]` entry, and restarts the clone via the same
  `_get_clone_client` path. No manual @BotFather paste needed. There's
  no equivalent for manual bots — `/delbot` then `/addbot` with a fresh
  token covers that case, same as before.

Unlike the reference, there's no separate `_pending_replace` /
paste-a-token fallback flow — that only existed there for manual bots,
which don't get a "replace" flow here at all (by design, per the header
comment).

## Update: loose ends closed (touch, interactive addbot, /titanium, last-active)

Four gaps found during a systematic pass against the reference were closed:

- **`touch_titanium_bot` wired in** — was defined in `database.py` but
  never called (dead code, since this project has no `get_job_client`
  equivalent to call it from). Now called from `_get_clone_client` every
  time a clone is fetched or started, so `last_used` actually reflects
  reality.
- **Interactive `/addbot`** — `/addbot` with no token, and the ➕ Add Bot
  button, now prompt "send me your token" and wait (via a
  `_pending_addbot` dict + two group=-5 catch-all handlers, mirroring
  the pattern already used for /cancel-style interrupts elsewhere in
  this file) instead of just printing static instructions. Times out
  after 120s; `/cancel` aborts early. `/addbot <token>` one-liner still
  works unchanged. The verify/save/boot pipeline is now a single shared
  `_do_add_bot()` used by all three entry points.
- **`/titanium` command** — added as a direct shortcut to the panel,
  alongside the existing Settings-menu button. Also added to the bot's
  command list (`main.py`'s `set_bot_commands_list`).
- **"Last active" in bot details** — `titanium_view` now shows a
  human-readable last-active timestamp sourced from the now-functional
  `last_used` field. (`last_validated`/`db_status` from the reference's
  detail view weren't added — those track a periodic re-validation job
  this project doesn't have; inventing the fields without real data
  behind them would be misleading.)

## What was intentionally NOT ported

- The reference's `_AUTOCREATE_PURPOSE` dict / `purpose="custom_bot"`
  branch — that's for Akbots' separate "My Bots → Add Auto Bot" plain
  forwarding-bot feature, which this project doesn't have. Every
  auto-created bot here becomes a Titanium clone, full stop.
- The reference's `titanium_disable` / bulk-disable-all flow — this
  project only ever had single-bot removal (`/delbot`, ❌ Remove), which
  already revokes the managed-bot token itself (see the earlier
  revocation update below), so a separate disable flow adds nothing new
  here.
- `get_job_client()` (flood-pool auto-selection) — Akbots-specific;
  see the header comment for why it doesn't map onto this project.

## Setup required (one-time, per bot owner — not per user)

In @BotFather's Mini App → this bot → Bot Settings, enable **"Bot
Management Mode"**. Without it, Telegram returns `CREATE_BOT_BLOCKED`
when a user taps Create — the auto-create button's own message already
warns about this and points back to `/addbot` as the always-works
fallback.

## Verification

`python3 -m py_compile titanium.py database.py main.py config.py
diskwala.py keep_alive.py` — all clean. `titanium.py` is now 859 lines
(up from 763 post-revocation, 624 pre-revocation, 383 pre-autocreate).
The interactive-addbot prompt bubble is now reused (edited in place)
across the whole verify → save → boot pipeline instead of leaving a
stray "send me your token" bubble behind, matching the reference's
same-bubble pattern.

Couldn't runtime-test the actual Telegram-side flow (no live bot token /
Managed-Bots-enabled account in this sandbox) — the HTTP parameter name
for `getManagedBotToken` (`user_id`) and its result shape are inferred
from the reference implementation's own caveat, not confirmed against
Telegram's official docs for this specific method. If it starts failing,
check the returned error's `description` field first.
