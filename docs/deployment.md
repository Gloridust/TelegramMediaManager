# Deployment

The app ships as a multi-arch image (`amd64` + `arm64`) on GHCR and runs with a
single `docker compose up`. This page covers general Docker, NAS specifics, HTTPS
and backups.

## Contents

- [Docker Compose (recommended)](#docker-compose-recommended)
- [Synology (Container Manager / DSM 7)](#synology)
- [QNAP (Container Station)](#qnap)
- [Plain `docker run`](#plain-docker-run)
- [Reverse proxy & HTTPS](#reverse-proxy--https)
- [Updating](#updating)
- [Backup & restore](#backup--restore)

## Docker Compose (recommended)

```bash
curl -O https://raw.githubusercontent.com/Gloridust/TelegramMediaManager/main/docker-compose.yml
docker compose up -d
```

Open `http://<host>:36091` and complete the setup wizard.

To send downloads to a specific location and set the mihomo secret, create a
`.env` next to the compose file (optional):

```dotenv
DOWNLOADS_DIR=/volume1/media/telegram
MIHOMO_SECRET=a-long-random-string
```

### Build locally instead of pulling

Edit `docker-compose.yml`: comment out the `image:` line under `app` and
uncomment the `build:` block, then `docker compose up -d --build`.

## Synology

DSM 7 with **Container Manager**:

1. Copy `docker-compose.yml` to a folder on the NAS, e.g. `/volume1/docker/tmm/`.
2. Edit the `app` volumes so downloads land on a share you can see in File Station:
   ```yaml
   volumes:
     - ./data:/data
     - /volume1/media/telegram:/downloads
   ```
3. Container Manager → **Project** → **Create** → point it at that folder → run.
4. Reach the panel at `http://<nas-ip>:36091`.

> If port `36091` is taken, change the left side of `"36091:36091"` to e.g. `"8137:36091"`.

## QNAP

Container Station 3 supports Compose apps:

1. Container Station → **Applications** → **Create** → paste the compose file.
2. Adjust the `/downloads` bind to a shared folder (e.g. `/share/Multimedia/telegram`).
3. Deploy, then open `http://<nas-ip>:36091`.

## Plain `docker run`

Without the proxy sidecar (point at your own proxy later, or go direct):

```bash
docker run -d --name tmm \
  -p 36091:36091 \
  -e TMM_MIHOMO_ENABLED=0 \
  -v "$PWD/data:/data" \
  -v "$PWD/downloads:/downloads" \
  gloridust/telegrammediamanager:latest
```

> The image is published to **Docker Hub** (`gloridust/telegrammediamanager`) and
> **GHCR** (`ghcr.io/gloridust/telegrammediamanager`) — use whichever you prefer.

## Reverse proxy & HTTPS

The panel speaks plain HTTP inside the container. To serve it over HTTPS, put a
reverse proxy in front and set `TMM_SECURE_COOKIE=1` on the app so the session
cookie gets the `Secure` flag.

WebSocket upgrade must be forwarded for live progress. Example **Caddy**:

```caddyfile
tmm.example.com {
    reverse_proxy 127.0.0.1:36091
}
```

Example **Nginx**:

```nginx
location / {
    proxy_pass http://127.0.0.1:36091;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
}
```

> Even behind HTTPS, treat the panel as sensitive — add proxy-level auth (basic
> auth, OAuth, Tailscale, etc.) if it's reachable from outside your LAN. See
> [security.md](security.md).

## Updating

```bash
docker compose pull
docker compose up -d
```

Your `./data` volume carries all state across upgrades. The SQLite schema
migrates automatically on start.

## Backup & restore

Everything important is in **`./data`** (SQLite DB, Telegram session, mihomo
config). Downloads are in `./downloads`.

```bash
docker compose down
tar czf tmm-backup.tar.gz data
# restore: tar xzf tmm-backup.tar.gz && docker compose up -d
```

> The Telegram session file in `data/sessions/` is a login credential — protect
> the backup accordingly.
