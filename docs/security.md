# Security

> **Read this before exposing the panel anywhere beyond your LAN.**

## The core risk

To download restricted media, the app logs in as **your Telegram user account**
(not just a bot). The session file in `data/sessions/` can read your private
chats, contacts and groups. Anyone who reaches the panel — or steals that file —
effectively has your Telegram account.

Treat this app like your password manager, not like a media server.

## What the app does

- **Password hashing** — PBKDF2-HMAC-SHA256, 200k iterations, per-user random salt.
- **Sessions** — server-side tokens in SQLite, `HttpOnly` `SameSite=Lax` cookie,
  7-day expiry; changing your password invalidates all sessions.
- **Login throttling** — per-IP attempt limit (default 5 / 5 min).
- **Path confinement** — the file browser cannot escape the working root.
- **Least exposure by default** — only the panel port is published; the mihomo
  proxy/controller ports stay on the internal Docker network.
- **No third-party calls** — the panel is fully self-contained; the frontend loads
  no external scripts, fonts or CDNs.

## What you must do

1. **Keep it off the open internet.** Bind to your LAN, or put it behind a VPN
   (WireGuard/Tailscale) or an authenticated reverse proxy. Do **not** port-forward
   `8080` to the world.
2. **Use HTTPS if it leaves the LAN.** Terminate TLS at a reverse proxy and set
   `TMM_SECURE_COOKIE=1`. See [deployment.md](deployment.md#reverse-proxy--https).
3. **Strong admin password.** It guards your Telegram account.
4. **Set `MIHOMO_SECRET`.** Don't leave it at `change-me`.
5. **Protect backups.** A backup of `data/` contains your Telegram session — store
   it encrypted.
6. **Revoke if unsure.** In Telegram: Settings → Devices → terminate the session
   this app created. The app's **登出 Telegram** does the same.

## Threat model, briefly

| Threat | Mitigation |
|---|---|
| Panel exposed to the internet | **Your responsibility** — LAN-only / VPN / auth proxy. |
| Brute-force login | Per-IP throttling; use a strong password. |
| Cookie theft over the wire | HTTPS + `TMM_SECURE_COOKIE=1`. |
| Session file exfiltration | Filesystem permissions on `data/`; encrypted backups. |
| Malicious link/path input | Server-side link parsing; path confinement to the root. |

## Reporting a vulnerability

Please report security issues privately — see [SECURITY.md](../SECURITY.md) in the
repository root. Do not open a public issue for a vulnerability.
