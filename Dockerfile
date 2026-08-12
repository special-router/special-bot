# syntax=docker/dockerfile:1
# Single image for all services (web, celery, beat, monitoring).
# Xray is included for the monitoring worker; queue routing enforces that
# ordinary workers never execute L2 probes.
#
# Both bases are pinned by digest. That is deliberate: a floating tag makes
# BuildKit issue an unauthenticated HEAD manifest request to Docker Hub, which
# Docker Hub answers with 429 regardless of the remaining pull quota. A pinned
# digest resolves from the local layer cache instead.
#
# Build: docker build -t vpnbot:latest .
# First time on a host, cache the bases:
#   docker pull python@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6
#   docker pull ghcr.io/xtls/xray-core:26.6.1@sha256:16786b44020e8f4c1ff3731c73cb46fe4e1e4e07af87a0daec920e24213bfbfc

FROM ghcr.io/xtls/xray-core:26.6.1@sha256:16786b44020e8f4c1ff3731c73cb46fe4e1e4e07af87a0daec920e24213bfbfc AS xray

FROM python:3.13-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq5 \
       curl \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Python deps (separate layer for caching)
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Xray binary (monitoring worker only; present but unused on other services)
COPY --from=xray /usr/local/bin/xray /usr/local/bin/xray

# Application source
COPY . .

RUN chmod +x /app/entrypoint.sh /usr/local/bin/xray \
    && /usr/local/bin/xray version

# Статика собирается в образ, а не при старте. entrypoint делает то же самое, но
# через `|| true`: там сбой сборки молча оставляет админку без единого стиля.
# Здесь он ломает сборку. Ни базы, ни `.environment` команде не нужно — все
# настройки, которые она читает, имеют значения по умолчанию.
RUN python manage.py collectstatic --noinput

# Overridden per service in docker-compose.deploy.yml
CMD ["python", "manage.py", "help"]


