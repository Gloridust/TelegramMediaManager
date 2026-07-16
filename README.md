# Telegram Media Manager

A Telegram Bot that collaborates with a User Client (Pseudo-User) to download media from restricted channels/groups.

## Features
- **Traditional Bot**: Interacts with you, manages settings.
- **User Client**: Logs in as your user account to access restricted content.
- **QR Login**: Easy login via QR code sent to the bot.
- **Link Processing**: Send message links to the bot, and the user client will download them.
- **Media Forwarding**: Forward media to the bot for direct download.
- **Resumable Downloads**: Files stream into `<name>.part` and continue from the last
  byte after a crash, a network drop, or a restart — nothing is re-downloaded.
- **Restart Recovery**: Queued jobs, channel scan progress and settings persist in
  `state.db`; on restart the bot offers to pick up exactly where it left off.
- **Interactive Menus**: Inline keyboards for the task list, settings (live concurrency
  tuning), folder browser, and retrying failed downloads.

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
5. **Track & Tune**:
   - Send `/tasks` (or tap **📥 任务列表**) to see what is downloading, queued, or failed,
     and to retry failures with one tap.
   - Send `/settings` (or tap **⚙️ 设置**) to change the concurrency live. Lowering it never
     interrupts a download in progress — surplus workers retire once they go idle.
6. **Cancel**:
   - Send `/cancel` to stop a running channel download and clear the queue. Partially
     downloaded data is kept so it can be resumed later.

## Resuming

Interrupted work is never lost:

- **Mid-file**: data is written to `<name>.part`. On the next attempt the download restarts
  from the size of that file (aligned down to 4 KB), so only the missing bytes are fetched.
- **Mid-queue**: every job is recorded in `state.db` before it starts. On restart the bot
  reports what is unfinished and offers **▶️ 继续下载** or **🗑 放弃并清空**.
- **Mid-channel**: a channel scan stores a cursor, so resuming continues from the last
  scanned message instead of re-walking the whole history.
- Send `/resume` at any time to requeue unfinished work.

### Moving the download tree

Paths are stored **relative to the working root**, never absolute, and always with `/`
separators. So if you move the whole tree — new drive letter, a remounted Docker volume,
a migration to another machine — just point the bot at the new location:

```
/setroot /new/path/to/media
```

Unfinished jobs relocate with it and resume against the real files. If the saved root is
missing at startup the bot says so instead of silently downloading into a fallback
directory. Paths outside the root can't be made portable and stay absolute.

Note that the **filesystem is the source of truth** for what is already downloaded: the
skip check looks for the file on disk, not at the database. Moving an individual finished
file out of its folder means re-sending that link downloads it again — which is also what
makes deleting a file a valid way to force a re-download.

## Commands
- `/start` — show the menu
- `/help` — list commands
- `/login` — QR login for the user client (asks for 2FA password if enabled)
- `/folders` — folder manager (create / switch the current download directory)
- `/tasks` — task list; retry failed downloads
- `/settings` — adjust concurrency (persisted across restarts)
- `/resume` — continue unfinished downloads
- `/download_channel` — bulk-download all media from a channel (into the current directory)
- `/setroot <folder>` — change the working root directory (`/setpath` is kept as an alias)
- `/status` — show login state, queue size and the current/root directories
- `/cancel` — cancel the channel download and clear the queue

## Notes
- The User Client runs locally on your machine/server.
- `API_ID` and `API_HASH` are mandatory for Telethon — get your own from [my.telegram.org](https://my.telegram.org).
- Downloads are **idempotent**: re-sending the same link skips files already saved.
- `MAX_CONCURRENT_DOWNLOADS` sets the *initial* concurrency (default 2); after that the
  value chosen in `/settings` wins. Keep it low to avoid Telegram flood limits.
- `state.db` holds queue/settings state and is safe to delete when the bot is stopped —
  you only lose the resume list, not the downloaded files.
