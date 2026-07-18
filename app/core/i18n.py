"""Backend message localization.

The panel UI is translated on the frontend; this catalog covers the messages the
*server* generates and returns (operation results, errors, notes) so they match
the user's chosen language. The choice is stored as the ``ui_lang`` setting and
kept in sync by the frontend.
"""

from app.config import Keys

DEFAULT_LANG = "en"

MESSAGES = {
    # --- download engine ---
    "not_authorized": {"zh": "用户端尚未登录", "en": "Telegram user client is not logged in"},
    "bad_message_link": {"zh": "无法解析有效的消息链接", "en": "Could not parse a valid message link"},
    "cannot_access_chat": {"zh": "无法访问该会话：{e}", "en": "Cannot access this chat: {e}"},
    "fetch_message_failed": {"zh": "获取消息失败：{e}", "en": "Failed to fetch the message: {e}"},
    "message_not_found": {"zh": "未找到该消息", "en": "Message not found"},
    "no_media": {"zh": "该消息没有可下载的媒体", "en": "This message has no downloadable media"},
    "channel_busy": {"zh": "已有频道下载在进行中，请先取消", "en": "A channel download is already running — cancel it first"},
    "bad_channel_link": {"zh": "无法解析有效的频道链接", "en": "Could not parse a valid channel link"},
    "cannot_access_channel": {"zh": "无法访问该频道：{e}", "en": "Cannot access this channel: {e}"},
    # --- telegram login ---
    "tfa_timeout": {"zh": "两步验证密码输入超时", "en": "Two-step verification password timed out"},
    "tfa_failed": {"zh": "两步验证失败：{e}", "en": "Two-step verification failed: {e}"},
    "qr_expired": {"zh": "二维码已过期，请重试", "en": "QR code expired, please retry"},
    "need_api_creds": {"zh": "请先填写 API ID / API Hash", "en": "Please set API ID / API Hash first"},
    "login_start_failed": {"zh": "无法开始登录：{e}", "en": "Could not start login: {e}"},
    "no_qr": {"zh": "当前没有可用的二维码", "en": "No QR code available right now"},
    "creds_saved_connect_failed": {"zh": "凭据已保存，但连接失败：{e}", "en": "Credentials saved, but connection failed: {e}"},
    "no_password_pending": {"zh": "当前不需要密码或登录流程未在等待", "en": "No password is expected right now"},
    # --- auth ---
    "already_setup": {"zh": "已完成初始化，无法重复设置", "en": "Setup is already complete"},
    "too_many_attempts": {"zh": "尝试过于频繁，请稍后再试", "en": "Too many attempts — try again later"},
    "bad_credentials": {"zh": "用户名或密码错误", "en": "Incorrect username or password"},
    "wrong_current_password": {"zh": "当前密码不正确", "en": "Current password is incorrect"},
    "not_logged_in": {"zh": "未登录", "en": "Not logged in"},
    "session_expired": {"zh": "会话已过期", "en": "Session expired"},
    "user_missing": {"zh": "用户不存在", "en": "User not found"},
    # --- files ---
    "invalid_folder": {"zh": "无效的文件夹名或非法路径", "en": "Invalid folder name or path"},
    "invalid_path": {"zh": "路径无效或超出工作根目录", "en": "Invalid path or outside the working root"},
    "invalid_path_detail": {"zh": "无效路径：{e}", "en": "Invalid path: {e}"},
    # --- settings / proxy / bot ---
    "proxy_applied": {"zh": "代理已应用，用户端已按新设置重连。", "en": "Proxy applied; the user client reconnected with the new settings."},
    "saved_reconnect_failed": {"zh": "设置已保存，但重连失败：{e}", "en": "Saved, but reconnect failed: {e}"},
    "bot_started": {"zh": "机器人已启动。", "en": "Bot started."},
    "bot_enable_missing": {"zh": "已启用，但缺少令牌或凭据，未能启动。", "en": "Enabled, but missing token or credentials — not started."},
    "bot_disabled": {"zh": "机器人已停用。", "en": "Bot stopped."},
    "mihomo_write_failed": {"zh": "写入 mihomo 配置失败：{e}", "en": "Failed to write mihomo config: {e}"},
    "proxy_reloaded": {"zh": "已重新加载代理配置", "en": "Proxy configuration reloaded"},
    "mihomo_reject": {"zh": "mihomo 拒绝重载（HTTP {code}）", "en": "mihomo refused reload (HTTP {code})"},
    "mihomo_unreachable": {"zh": "无法连接 mihomo：{e}", "en": "Cannot reach mihomo: {e}"},
}

SUPPORTED = ("zh", "en")


def t(lang, key, **kw):
    entry = MESSAGES.get(key)
    if not entry:
        return key
    s = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    return s.format(**kw) if kw else s


async def tr(store, key, **kw):
    """Translate using the stored ``ui_lang`` setting."""
    lang = await store.get_setting(Keys.UI_LANG, DEFAULT_LANG)
    return t(lang, key, **kw)
