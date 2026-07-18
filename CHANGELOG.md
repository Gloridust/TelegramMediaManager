# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [2.0.0] — 2026-07-18

A ground-up rework from a single-file Telegram bot into a self-hosted app with a
web panel, Docker deployment and a bundled proxy.

### Added
- **Web panel** — Telegram-style single-page UI (vanilla JS, no build step) with
  light/dark themes, a first-run setup wizard, session auth, and live download
  progress over WebSocket.
- **One-command Docker** — multi-stage, multi-arch (`amd64` + `arm64`) image and a
  `docker-compose.yml` for NAS-friendly deployment. No `.env` required.
- **mihomo (Clash Meta) sidecar** — paste a `vless`/subscription link and route
  all traffic through it; list, select and latency-test nodes from the panel.
  External SOCKS5/HTTP proxies are supported too.
- **FastAPI service core** — a UI-agnostic engine shared by the web API and the
  Telegram bot, so their state stays consistent.
- **Account management** — PBKDF2-hashed admin login, server-side sessions, login
  throttling, path-confined file browser.
- **Test suite** — pytest with FastAPI `TestClient` and fake Telethon clients;
  CI on Python 3.11 & 3.12; GHCR image publishing workflow.

### Changed
- Configuration moved from a required `.env` to the web panel, persisted in
  SQLite under the data volume.
- The Telegram bot is now an optional thin controller over the shared engine
  rather than the whole application.

### Carried over from 1.x
- Byte-level resumable downloads (`.part` files), persistent jobs with
  restart-time resume, channel scans with a durable cursor, and portable
  (root-relative) storage paths.

## [1.x]

The original single-file Telegram bot: link/album/channel downloads, resumable
transfers, persistent state and an inline-keyboard menu. See the git history on
the `main` branch prior to 2.0.
