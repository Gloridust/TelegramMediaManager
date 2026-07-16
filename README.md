# Telegram Media Manager

A Telegram Bot that collaborates with a User Client (Pseudo-User) to download media from restricted channels/groups.

## Features
- **Traditional Bot**: Interacts with you, manages settings.
- **User Client**: Logs in as your user account to access restricted content.
- **QR Login**: Easy login via QR code sent to the bot.
- **Link Processing**: Send message links to the bot, and the user client will download them.
- **Media Forwarding**: Forward media to the bot for direct download.

## Setup

1. **Install Requirements**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configuration**
   Copy `.env.example` to `.env` and fill in your details:
   - `API_ID` & `API_HASH`: Get from [my.telegram.org](https://my.telegram.org)
   - `BOT_TOKEN`: Get from @BotFather
   - `ADMIN_ID`: Your Telegram User ID (get from @userinfobot)

3. **Run**
   ```bash
   python main.py
   ```

## Usage

1. **Start the Bot**: Run the script. The bot will notify you (Admin ID) when it starts.
2. **Login User Client**:
   - Send `/login` to the bot.
   - The bot will send a QR code image.
   - Scan it with your Telegram App (Settings -> Devices -> Link Desktop Device).
   - If you have 2FA password, the bot will ask for it. Send it to the bot.
3. **Download Media**:
   - **Public/Allowed Content**: Forward messages to the bot.
   - **Restricted Content**: Copy the message link (e.g., `https://t.me/c/xxxx/xxx`) and send it to the bot.
4. **Manage Folders**:
   - Send `/folders` (or tap **🗂 文件夹管理** in the menu) to open the folder browser.
   - Browse into subfolders, create new ones (**➕ 新建文件夹**), and pick the active
     download directory (**✅ 设为下载目录**). New downloads always go to the current directory.
   - All folders live under the working root; the browser cannot escape it.
5. **Cancel**:
   - Send `/cancel` to stop a running channel download and clear the queue.

## Commands
- `/start` — show the menu
- `/help` — list commands
- `/login` — QR login for the user client (asks for 2FA password if enabled)
- `/folders` — folder manager (create / switch the current download directory)
- `/download_channel` — bulk-download all media from a channel (into the current directory)
- `/setroot <folder>` — change the working root directory (`/setpath` is kept as an alias)
- `/status` — show login state, queue size and the current/root directories
- `/cancel` — cancel the channel download and clear the queue

## Notes
- The User Client runs locally on your machine/server.
- `API_ID` and `API_HASH` are mandatory for Telethon — get your own from [my.telegram.org](https://my.telegram.org).
- Downloads are **idempotent**: re-sending the same link skips files already saved.
- `MAX_CONCURRENT_DOWNLOADS` controls how many files download at once (default 2). Keep it low to avoid Telegram flood limits.
