# syntax=docker/dockerfile:1
# Single image for all services (web, celery, beat, monitoring).
# Xray is included for the monitoring worker; queue routing enforces that
# ordinary workers never execute L2 probes.
#
# Build: docker build --pull=never -t vpnbot:latest .
# (python:3.13-slim must be present in the local layer cache first;
#  pull it once with: docker pull python:3.13-slim)

FROM ghcr.io/xtls/xray-core:26.6.1@sha256:16786b44020e8f4c1ff3731c73cb46fe4e1e4e07af87a0daec920e24213bfbfc AS xray

FROM python:3.13-slim

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

# Overridden per service in docker-compose.deploy.yml
CMD ["python", "manage.py", "help"]


