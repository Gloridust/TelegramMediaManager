# Contributing

Thanks for your interest in improving Telegram Media Manager! This project aims to
be a friendly, well-tested self-hosted app — contributions of all sizes are
welcome.

## Ground rules

- Be respectful (see the [Code of Conduct](CODE_OF_CONDUCT.md)).
- Keep pull requests focused; one concern per PR is easiest to review.
- Discuss large changes in an issue first so we can align on direction.

## Development setup

```bash
git clone https://github.com/Gloridust/TelegramMediaManager.git
cd TelegramMediaManager
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest

python main.py        # panel at http://localhost:36091
pytest -q             # run tests
```

State is written to `./data` by default. Set `TMM_MIHOMO_ENABLED=0` when running
outside Docker so the app doesn't try to reach a mihomo sidecar.

## Project layout

```
app/core/   UI-agnostic engine: store, telegram, downloader, proxy, events, services
app/api/    FastAPI routers, auth, WebSocket
app/bot/    optional Telegram bot controller
web/        vanilla-JS single-page panel (no build step)
docker/     Dockerfile
docs/       documentation
tests/      pytest suite
```

See [docs/architecture.md](docs/architecture.md) for how it fits together.

## Guidelines

- **Keep the core UI-agnostic.** Business logic lives in `app/core`; the API and
  bot are thin. New features usually mean a method on the engine plus a route and
  a bit of UI.
- **No frontend build step.** The panel is plain ES modules + CSS, served static.
  Please keep it dependency-free and self-contained (no CDNs).
- **Match the existing style.** Small, focused functions; comments explain *why*,
  not *what*.
- **Add or update tests** for behaviour changes. The suite uses `pytest` with
  FastAPI's `TestClient` and fake Telethon clients — no network needed.
- **Update docs** when you change configuration, endpoints or behaviour.

## Submitting a PR

1. Fork and branch from `main` (or the active `v2-*` branch).
2. Make your change with tests.
3. Run `pytest -q` and, if you touched the image, `docker build -f docker/Dockerfile .`.
4. Open a PR describing the change and the reasoning. Link any related issue.

## Reporting bugs / requesting features

Use the issue templates. For bugs, include your deployment method (compose /
`docker run` / bare), logs (`docker compose logs app`), and steps to reproduce.

Security issues: **do not** open a public issue — see [SECURITY.md](SECURITY.md).
