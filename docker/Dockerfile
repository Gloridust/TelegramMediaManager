# syntax=docker/dockerfile:1
# Multi-stage, multi-arch (amd64 + arm64) image for TelegramMediaManager.
# All Python deps ship prebuilt wheels for both arches, so no compilation runs
# here — the build is fast and the final image stays small.

FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -U pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim
LABEL org.opencontainers.image.title="TelegramMediaManager" \
      org.opencontainers.image.description="Self-hosted Telegram media downloader with a web panel" \
      org.opencontainers.image.source="https://github.com/Gloridust/TelegramMediaManager"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TMM_DATA_DIR=/data \
    TMM_DOWNLOADS_DIR=/downloads \
    TMM_PORT=8080

# curl for the healthcheck; tini for correct signal handling / zombie reaping.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY app ./app
COPY web ./web
COPY main.py ./
RUN mkdir -p /data /downloads

EXPOSE 8080
VOLUME ["/data", "/downloads"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:${TMM_PORT}/api/health || exit 1

ENTRYPOINT ["tini", "--"]
CMD ["python", "main.py"]
