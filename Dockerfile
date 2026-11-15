# syntax=docker/dockerfile:1
# Pyxida: Python app + Codex CLI (Node) in one image. Data and the Codex sign-in live in /data.
FROM node:22-bookworm-slim

# Codex CLI release to bundle. Override with --build-arg CODEX_VERSION=x.y.z.
ARG CODEX_VERSION=0.166.0

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    HOST=0.0.0.0 \
    PORT=8765 \
    DATA_DIR=/data \
    CODEX_HOME=/data/codex \
    TZ=Europe/Zurich

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates tzdata tini \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && codex --version

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data/codex && chown -R node:node /data

USER node
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('PORT', '8765'), timeout=4)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "app.py"]
