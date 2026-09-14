#!/usr/bin/env python3
"""
telegram_digest.py

Exports messages from every Telegram channel/group you're in and writes
them to files you can upload straight into a normal Claude.ai chat (free)
to get a summary — no API key needed.

SETUP
-----
1. Install dependencies:
       pip install telethon --break-system-packages

2. Get Telegram API credentials (one-time, free):
       - Go to https://my.telegram.org -> "API development tools"
       - Log in with your phone number
       - Create an app, copy the api_id and api_hash

3. Set environment variables in PowerShell (current session):
       $env:TELEGRAM_API_ID = "123456"
       $env:TELEGRAM_API_HASH = "your_api_hash_here"
       $env:TELEGRAM_PHONE = "+972501234567"

4. Run it, choosing how far back to look with --range:
       python telegram_digest.py --range unread      (default: only unread)
       python telegram_digest.py --range today        (since 00:00 today)
       python telegram_digest.py --range 24h           (last 24 hours)
       python telegram_digest.py --range week          (since 00:00 Sunday)
       python telegram_digest.py --range weekback       (7 days back from now)

   By default, every message it exports is marked as read afterwards
   (same as if you'd opened the chat). To leave everything unread, add:
       python telegram_digest.py --range today --keep-unread

   The first run will ask for a login code sent to your Telegram app
   (and your 2FA password if you have one). After that, a session
   file (telegram_digest.session) is saved locally so you won't be
   asked again.

5. Open the file it creates at ./exports/_ALL_DIGEST.txt, and either
   paste its contents into a Claude.ai chat, or upload the file directly
   and ask Claude to summarize it. That's it — completely free.

NOTES
-----
- Chats are scanned concurrently (see DIALOG_CONCURRENCY below, default 8) to
  speed things up. If you have a lot of chats and start hitting Telegram
  flood-wait errors, lower that number.
- This uses your real Telegram account (via Telethon / MTProto), not
  a bot, because bots cannot see channels/groups they weren't added
  to as admins. Only run this on a machine you trust — the session
  file it creates grants full access to your account, same as being
  logged in.
- By default this only processes channels/groups, not 1:1 DMs.
  Set INCLUDE_DIRECT_MESSAGES = True below to include DMs too.
- Per-chat files land in ./exports/, plus one combined file
  (_ALL_DIGEST.txt) that's easiest to paste/upload in one go.
- OPTIONAL: if you ever get an Anthropic API key, set ANTHROPIC_API_KEY
  and this script will call the API directly and skip the manual step.
  Not required.
"""

import os
import re
import getpass
import argparse
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone

from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from telethon.errors import SessionPasswordNeededError

# ---------------------------------------------------------------------------
# CONFIG — edit these or set the equivalent environment variables
# ---------------------------------------------------------------------------
API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
PHONE = os.environ.get("TELEGRAM_PHONE", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # optional, see NOTES

SESSION_NAME = "telegram_digest"          # local session file, keep private
EXPORT_DIR = Path("exports")
COMBINED_FILE = EXPORT_DIR / "_ALL_DIGEST.txt"
MAX_MESSAGES_PER_CHAT = 500                # safety cap per chat
INCLUDE_DIRECT_MESSAGES = False            # channels/groups only by default
CLAUDE_MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ]", "_", name).strip()
    return name[:80] or "unnamed_chat"


async def qr_login_flow(client):
    """Log in by scanning a QR code instead of receiving a numeric code.
    In Telegram on your phone: Settings -> Devices -> Link Desktop Device."""
    import qrcode

    qr_login = await client.qr_login()
    while True:
        print(
            "\nOn your phone, open Telegram -> Settings -> Devices -> "
            "Link Desktop Device, then scan this QR code:\n",
            flush=True,
        )
        qr = qrcode.QRCode(border=1)
        qr.add_data(qr_login.url)
        qr.print_ascii(invert=True)
        print("\nWaiting for you to scan it (expires in ~60s)...", flush=True)
        try:
            await qr_login.wait(timeout=60)
            break
        except asyncio.TimeoutError:
            print("QR code expired — generating a new one...", flush=True)
            await qr_login.recreate()
        except SessionPasswordNeededError:
            password = getpass.getpass("Two-factor password: ")
            await client.sign_in(password=password)
            break


def get_range_start(range_key: str):
    """Return a UTC datetime cutoff for the given --range, or None for 'unread'."""
    now_local = datetime.now().astimezone()

    if range_key == "unread":
        return None
    if range_key == "today":
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    elif range_key == "24h":
        start_local = now_local - timedelta(hours=24)
    elif range_key == "week":
        # Most recent Sunday at 00:00. Python weekday(): Mon=0 ... Sun=6.
        days_since_sunday = (now_local.weekday() + 1) % 7
        start_local = (now_local - timedelta(days=days_since_sunday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif range_key == "weekback":
        start_local = now_local - timedelta(days=7)
    else:
        raise ValueError(f"Unknown range: {range_key}")

    return start_local.astimezone(timezone.utc)


async def fetch_dialog_messages(client, dialog, range_key, start_time):
    """Return messages for one dialog (oldest -> newest) matching the range."""
    if range_key == "unread":
        if dialog.unread_count <= 0:
            return []
        limit = min(dialog.unread_count, MAX_MESSAGES_PER_CHAT)
        messages = await client.get_messages(dialog.id, limit=limit)
        return list(reversed(messages))

    messages = []
    async for msg in client.iter_messages(dialog, limit=MAX_MESSAGES_PER_CHAT):
        if msg.date < start_time:
            break
        messages.append(msg)
    messages.reverse()  # oldest -> newest
    return messages


async def format_messages(messages):
    lines = []
    sender_cache = {}  # sender_id -> resolved display name, avoids re-fetching per message
    for msg in messages:
        if not msg or (not msg.text and not msg.message):
            continue
        sender_id = msg.sender_id
        if sender_id not in sender_cache:
            name = "Unknown"
            try:
                sender_entity = await msg.get_sender()
                if sender_entity:
                    name = getattr(sender_entity, "title", None) or \
                            " ".join(filter(None, [
                                getattr(sender_entity, "first_name", None),
                                getattr(sender_entity, "last_name", None),
                            ])) or getattr(sender_entity, "username", "Unknown")
            except Exception:
                pass
            sender_cache[sender_id] = name
        sender = sender_cache[sender_id]
        ts = msg.date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        text = msg.text or msg.message or "[non-text message]"
        lines.append(f"[{ts}] {sender}: {text}")
    return lines


DIALOG_CONCURRENCY = 8  # how many chats to process at once; higher = faster but more flood-wait risk


async def process_dialog(client, dialog, range_key, start_time, keep_unread, sem, index, total):
    """Fetch, format, export, and (optionally) mark-read a single dialog. Bounded by sem."""
    async with sem:
        label = f"[{index}/{total}]"
        entity = dialog.entity
        is_group_or_channel = isinstance(entity, (Channel, Chat))

        if not is_group_or_channel and not INCLUDE_DIRECT_MESSAGES:
            print(f"{label} Skipping '{dialog.name}' (1:1 DM, not included)", flush=True)
            return {"status": "skipped_dm", "name": dialog.name}

        print(f"{label} Checking '{dialog.name}'...", flush=True)
        try:
            messages = await fetch_dialog_messages(client, dialog, range_key, start_time)
        except Exception as e:
            print(f"{label}   Failed to fetch messages for '{dialog.name}': {e}", flush=True)
            return {"status": "skipped_empty", "name": dialog.name}

        if not messages:
            print(f"{label}   No matching messages, skipping.", flush=True)
            return {"status": "skipped_empty", "name": dialog.name}

        print(f"{label}   Found {len(messages)} message(s), formatting...", flush=True)
        lines = await format_messages(messages)
        if not lines:
            print(f"{label}   Nothing text-based to export, skipping.", flush=True)
            return {"status": "skipped_empty", "name": dialog.name}

        filename = EXPORT_DIR / f"{sanitize_filename(dialog.name)}.txt"
        filename.write_text("\n".join(lines), encoding="utf-8-sig")
        print(f"{label}   Exported {len(lines)} message(s) -> {filename}", flush=True)

        if not keep_unread:
            try:
                newest_id = messages[-1].id
                await client.send_read_acknowledge(dialog, max_id=newest_id)
                print(f"{label}   Marked as read.", flush=True)
            except Exception as e:
                print(f"{label}   Could not mark '{dialog.name}' as read: {e}", flush=True)
        else:
            print(f"{label}   Left as unread (--keep-unread set).", flush=True)

        return {
            "status": "exported",
            "name": dialog.name,
            "filename": filename,
            "lines": lines,
        }


async def export_messages(client, range_key, keep_unread):
    """Fetch messages for each qualifying dialog per range_key, write to files."""
    EXPORT_DIR.mkdir(exist_ok=True)
    start_time = get_range_start(range_key)
    if start_time:
        print(f"Cutoff time: {start_time.strftime('%Y-%m-%d %H:%M UTC')} (range: {range_key})\n", flush=True)
    else:
        print(f"Mode: unread messages only\n", flush=True)

    print("Loading your chat list from Telegram...", flush=True)
    dialogs = await client.get_dialogs()
    print(f"Found {len(dialogs)} total chats. Scanning (up to {DIALOG_CONCURRENCY} at a time)...\n", flush=True)

    sem = asyncio.Semaphore(DIALOG_CONCURRENCY)
    tasks = [
        process_dialog(client, dialog, range_key, start_time, keep_unread, sem, i, len(dialogs))
        for i, dialog in enumerate(dialogs, start=1)
    ]
    results = await asyncio.gather(*tasks)

    exported_files = []
    combined_chunks = []
    skipped_dm = 0
    skipped_empty = 0

    # Preserve original dialog order in the combined digest, even though fetches ran concurrently.
    for r in results:
        if r["status"] == "exported":
            exported_files.append((r["name"], r["filename"]))
            combined_chunks.append(f"===== {r['name']} =====\n" + "\n".join(r["lines"]))
        elif r["status"] == "skipped_dm":
            skipped_dm += 1
        else:
            skipped_empty += 1

    print(
        f"\nScan complete: {len(exported_files)} chat(s) exported, "
        f"{skipped_dm} DM(s) skipped, {skipped_empty} chat(s) had nothing matching.",
        flush=True,
    )

    if combined_chunks:
        print(f"Writing combined digest to {COMBINED_FILE}...", flush=True)
        COMBINED_FILE.write_text("\n\n".join(combined_chunks), encoding="utf-8-sig")

    return exported_files


def summarize_all_via_api(exported_files):
    """OPTIONAL path — only runs if you set ANTHROPIC_API_KEY yourself."""
    import anthropic
    SUMMARY_DIR = Path("summaries")
    SUMMARY_DIR.mkdir(exist_ok=True)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    for chat_name, filepath in exported_files:
        print(f"Summarizing '{chat_name}'...")
        content = filepath.read_text(encoding="utf-8")
        prompt = (
            f"Below are messages from the Telegram chat '{chat_name}'. "
            "Summarize what's happened: key topics, any decisions or action items, "
            "and anything that looks like it needs my response. Keep it tight — "
            "bullet points, no fluff.\n\n"
            f"{content}"
        )
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = "".join(block.text for block in response.content if block.type == "text")
        except Exception as e:
            print(f"  Failed to summarize '{chat_name}': {e}")
            continue

        out_path = SUMMARY_DIR / f"{sanitize_filename(chat_name)}_summary.txt"
        out_path.write_text(summary, encoding="utf-8")
        print(f"  -> {out_path}")


def parse_args():
    parser = argparse.ArgumentParser(description="Export Telegram messages for summarizing with Claude.")
    parser.add_argument(
        "--range",
        choices=["unread", "today", "24h", "week", "weekback"],
        default="unread",
        help=(
            "How far back to fetch: 'unread' (default, only unread messages), "
            "'today' (since 00:00 today), '24h' (last 24 hours), "
            "'week' (since 00:00 Sunday), 'weekback' (7 days back from now)."
        ),
    )
    parser.add_argument(
        "--keep-unread",
        action="store_true",
        help="Don't mark exported messages as read (default marks them as read).",
    )
    parser.add_argument(
        "--qr",
        action="store_true",
        help="Log in via QR code (scan with Telegram: Settings > Devices > Link Desktop Device) "
             "instead of a phone code. Use this if you're not receiving the phone login code.",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    if not API_ID or not API_HASH or not PHONE:
        raise SystemExit(
            "Missing Telegram credentials. Set TELEGRAM_API_ID, TELEGRAM_API_HASH "
            "and TELEGRAM_PHONE (see the setup instructions at the top of this file)."
        )

    print("Connecting to Telegram...", flush=True)
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        print("Already logged in via saved session — no code needed.", flush=True)
    elif args.qr:
        await qr_login_flow(client)
        print("Logged in successfully via QR code.\n", flush=True)
    else:
        print("Not yet authorized — Telegram will send a login code now. Check your Telegram app.", flush=True)
        await client.start(phone=PHONE)
        print("Logged in successfully.\n", flush=True)

    print(f"Fetching messages (range: {args.range})...\n", flush=True)
    exported_files = await export_messages(client, args.range, args.keep_unread)

    print("\nClosing connection to Telegram...", flush=True)
    await client.disconnect()

    if not exported_files:
        print("No matching messages found.", flush=True)
        return

    if ANTHROPIC_API_KEY:
        print("ANTHROPIC_API_KEY found — summarizing via API...\n", flush=True)
        summarize_all_via_api(exported_files)
        print("\nDone. See ./exports for raw text and ./summaries for summaries.", flush=True)
    else:
        print(
            f"\nNo API key set (that's fine — this is the free path).\n"
            f"Open {COMBINED_FILE} and paste its contents (or upload the file) "
            f"into a Claude.ai chat, then ask for a summary.\n",
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
