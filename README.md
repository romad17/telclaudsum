# Telegram Digest

Export recent messages from your Telegram channels and groups into neat text files designed for easy summarizing using Claude.ai (free) or the Anthropic API.

---

## Features

- **No Paid API Needed**: Exports formatted text files ready to upload or paste straight into a free [Claude.ai](https://claude.ai) chat.
- **Flexible Time Ranges**: Filter messages by `unread`, `today`, `24h`, `week`, or `weekback`.
- **Automated Mark-as-Read**: Automatically marks processed messages as read (with an option to keep them unread).
- **Multiple Login Methods**: Login using standard SMS/app login codes or via QR code scanning.
- **Single or Combined Outputs**: Outputs individual chat logs in `./exports/` as well as a consolidated `_ALL_DIGEST.txt` file for one-click upload.
- **Optional API Integration**: If an `ANTHROPIC_API_KEY` is detected, the script can automatically summarize all exported chats for you.

---

## Requirements

- Python 3.8+
- A Telegram account
- Telegram API Credentials (`api_id` and `api_hash`)

---

## Setup & Installation

### 1. Install Dependencies

Install Telethon (and `qrcode` if using QR code login):

```bash
pip install telethon qrcode --break-system-packages
```
*(Note: `--break-system-packages` is optional depending on your Python environment).*

### 2. Get Telegram API Credentials

1. Go to [my.telegram.org](https://my.telegram.org) and log in with your phone number.
2. Select **API development tools**.
3. Fill in the form to create a new application.
4. Copy your `api_id` and `api_hash`.

### 3. Set Environment Variables

Set your environment variables before running the script.

#### PowerShell (Windows):
```powershell
$env:TELEGRAM_API_ID = "123456"
$env:TELEGRAM_API_HASH = "your_api_hash_here"
$env:TELEGRAM_PHONE = "+972501234567"
```

#### Bash / Zsh (Linux & macOS):
```bash
export TELEGRAM_API_ID="123456"
export TELEGRAM_API_HASH="your_api_hash_here"
export TELEGRAM_PHONE="+972501234567"
```

---

## Usage

Run the script specifying the time range you wish to export:

```bash
python telegram_digest.py --range unread
```

### Options & Arguments

| Argument | Description |
| :--- | :--- |
| `--range unread` | Export only unread messages *(Default)*. |
| `--range today` | Export all messages sent since 00:00 today. |
| `--range 24h` | Export messages from the last 24 hours. |
| `--range week` | Export messages since 00:00 Sunday. |
| `--range weekback` | Export messages from 7 days ago to present. |
| `--keep-unread` | Prevents marking exported messages as read on Telegram. |
| `--qr` | Displays a QR code in the terminal to scan with the Telegram app instead of entering a login code. |

### Examples

**Export today's messages without marking them as read:**
```bash
python telegram_digest.py --range today --keep-unread
```

**Log in using QR code scan:**
```bash
python telegram_digest.py --range 24h --qr
```

---

## Output Files

After running, the script creates an `exports/` folder:
- `exports/[Chat_Name].txt`: Individual export for each chat.
- `exports/_ALL_DIGEST.txt`: Combined export containing all extracted chat messages.

### How to Summarize (Free Method)
1. Open `./exports/_ALL_DIGEST.txt`.
2. Upload the file or paste its contents into [Claude.ai](https://claude.ai).
3. Prompt Claude: *"Please summarize the key topics, action items, and important updates from these messages."*

---

## Security & Privacy Notice

- **Session File**: The script generates a local `telegram_digest.session` file. This file stores your authentication state and allows full account access. **Do not share this file or commit it to a public repository.**
- **User vs. Bot Account**: This script connects as a real user client via Telethon/MTProto. This is necessary because Telegram bots cannot read messages in channels or groups unless added as explicit administrators.
