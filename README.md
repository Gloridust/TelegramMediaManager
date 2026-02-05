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
4. **Set Download Path**:
   - Send `/setpath <folder_name>` to change the save directory.

## Notes
- The User Client runs locally on your machine/server.
- `API_ID` and `API_HASH` are mandatory for Telethon.
