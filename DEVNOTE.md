# Developer Notes & Logic Summary

## 1. Project Overview
This project is a Telegram Media Manager that combines a traditional **Bot** (for interaction) and a **User Client** (for accessing restricted content). It allows users to download media from private channels or groups where forwarding/downloading is restricted by using a "Pseudo-User" session.

## 2. Architecture

### Components
- **`main.py`**: The entry point. Initializes the `TelegramMediaManager` and runs the asyncio event loop.
- **`manager.py`**: Contains the core logic class `TelegramMediaManager`.
    - Manages two Telethon clients: `self.bot` (Bot API) and `self.user_client` (MTProto User Session).
    - Handles command dispatching, login flows, link parsing, and download management.
    - Implements an async worker pool for concurrent downloads.
- **`config.py`**: Loads environment variables (`.env`) into a static `Config` class.

### Key Logic Flows

#### A. Dual-Client System
- **Bot**: Used for user interaction (commands like `/login`, `/setpath`, status updates). It listens to messages from `ADMIN_ID`.
- **User Client**: Used for "heavy lifting". It logs in as a real user to bypass bot restrictions (e.g., accessing private channels, downloading from restricted groups).

#### B. QR Code Login (`perform_qr_login`)
1. User sends `/login` to Bot.
2. Bot triggers `user_client.qr_login()`.
3. `qrcode` library generates a QR image from the login URL.
4. Bot sends the QR image to the user.
5. User scans it with their mobile Telegram app.
6. If 2FA is enabled, Bot asks for the password and completes the login.
7. Session is saved locally (`user_session.session`).

#### C. Download Queue & Workers (`download_worker`)
- **Queue**: `asyncio.Queue` holds pending download tasks.
- **Workers**: Fixed number of concurrent workers (default 2, configurable via `MAX_CONCURRENT_DOWNLOADS`).
- **Logic**:
    1. Workers fetch tasks `(message, event, client_type, folder, status_msg)`.
    2. Execute `_safe_download`.
    3. Update status message (Start -> Saved/Failed).
    4. Handle errors and cleanup.

#### D. Link Processing (`handle_link`)
- Detects if a text message contains a `t.me/` link.
- **Smart Filtering**: Ignores links inside captions of media messages (to avoid downloading ads). Only processes "pure" text links or links explicitly sent by the user.
- **Album Support**:
    - If a link points to a message that is part of an album (`grouped_id` exists), the system automatically fetches surrounding messages (±10 range).
    - Filters messages with the same `grouped_id` and queues all of them for download.

#### E. Channel Bulk Download (`start_channel_download`)
- Command: `/download_channel`.
- User provides a link to *any* message in the channel.
- System resolves the channel entity and creates a dedicated folder.
- Iterates through **all history** (`iter_messages`).
- Downloads every media found into the folder.

## 3. Optimization & User Experience (UX)

### Implemented Improvements
- **Safe Download**: `_safe_download` ensures unique filenames (auto-incrementing) and deletes partial files if the download fails/crashes.
- **Concurrency Control**: Prevents account flooding/ban by limiting simultaneous downloads.
- **Status Feedback**: Real-time edits to the status message let the user know what's happening (Queued -> Downloading -> Saved).
- **Clean Interface**: Bot ignores irrelevant links in captions.

### Future Improvements (Roadmap)
1.  **Progress Bar**:
    - Currently, the status says "Downloading...". Telethon supports a `progress_callback`.
    - *Action*: Implement a callback that edits the message with percentage (e.g., "Downloading... 45%").
    - *Note*: Need to throttle edits to avoid "FloodWait" errors (Telegram allows editing ~once per second).

2.  **Cancel Functionality**:
    - Add a `/cancel` command to stop the current download or clear the queue.
    - Requires tracking `asyncio.Task` objects.

3.  **Duplicate Detection**:
    - Add a database (SQLite) or file hash check to skip already downloaded files, even if filenames differ.

4.  **Filter Types**:
    - Allow users to specify `/download_channel --video-only` or `--images-only`.

5.  **Interactive Menus**:
    - Use Inline Buttons for confirmation (e.g., "Found 5000 files. Download? [Yes] [No]").

## 4. Maintenance Guide
- **Dependencies**: Keep `requirements.txt` updated. Critical libs: `telethon`, `pillow`, `qrcode`.
- **Session Files**: `*.session` files contain sensitive login data. **NEVER** commit them to Git.
- **API Limits**: Be aware of Telegram's flood limits. The current worker system helps, but aggressive usage can still trigger temporary bans.
