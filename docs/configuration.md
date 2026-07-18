# Configuration

Two tiers, deliberately separated:

- **Panel settings** — Telegram credentials, proxy, bot, folders, concurrency,
  password. Edited in the web UI, stored in `./data/state.db`. **No file editing.**
- **Environment variables** — infrastructure wiring only (paths, port, mihomo
  endpoint). Set on the container; rarely changed.

## Environment variables

All optional. Defaults are for the Docker image.

| Variable | Default | Purpose |
|---|---|---|
| `TMM_DATA_DIR` | `/data` | State dir: DB, sessions, mihomo config. |
| `TMM_DOWNLOADS_DIR` | `/downloads` | Default download root (bind-mount to your share). |
| `TMM_HOST` | `0.0.0.0` | Bind address inside the container. |
| `TMM_PORT` | `8080` | HTTP port inside the container. |
| `TMM_SECURE_COOKIE` | `0` | `1` adds the `Secure` flag to the session cookie (set behind HTTPS). |
| `TMM_SESSION_TTL` | `604800` | Panel session lifetime, seconds (7 days). |
| `TMM_LOGIN_MAX_ATTEMPTS` | `5` | Failed logins per window before throttling. |
| `TMM_LOGIN_WINDOW` | `300` | Throttle window, seconds. |
| `TMM_MIHOMO_ENABLED` | `1` | Whether the mihomo sidecar is present. Set `0` to disable proxy integration. |
| `TMM_MIHOMO_CONTROLLER` | `http://mihomo:9090` | mihomo REST controller URL. |
| `TMM_MIHOMO_HOST` | `mihomo` | Host the app dials for the proxy. |
| `TMM_MIHOMO_PORT` | `7890` | mihomo mixed (SOCKS/HTTP) port. |
| `TMM_MIHOMO_SECRET` | *(empty)* | Shared secret for the mihomo API. Set a strong value. |
| `TMM_MIHOMO_CONFIG_PATH` | `/root/.config/mihomo/config.yaml` | Config path **as mihomo sees it**, for reloads. |

Compose-level variables (read by `docker-compose.yml`):

| Variable | Purpose |
|---|---|
| `DOWNLOADS_DIR` | Host path bind-mounted to `/downloads`. |
| `MIHOMO_SECRET` | Sets `TMM_MIHOMO_SECRET`; keep app and sidecar in sync (they already share this). |

## Panel settings

Configured in the UI once you're logged in:

- **Telegram** — `API ID` / `API Hash` (from [my.telegram.org](https://my.telegram.org)),
  then QR + optional 2FA login.
- **Proxy** — subscription (mihomo) or an external SOCKS5/HTTP proxy. See [proxy.md](proxy.md).
- **Bot** *(optional)* — bot token + your admin user ID, to control from Telegram.
- **Concurrency** — simultaneous downloads (1–8). Lowering it never interrupts an
  in-progress file.
- **Working root** — the base of the download tree. Paths are stored relative to
  it, so moving the tree only means updating this.
- **Password** — updating it logs out all sessions.

## Where state lives

```
data/
├── state.db            SQLite: jobs, channels, settings, users, sessions
├── state.db-wal        write-ahead log
├── sessions/           Telegram .session files (login credentials!)
└── mihomo/config.yaml  generated mihomo configuration
downloads/              your media (or a bind-mounted NAS share)
```
