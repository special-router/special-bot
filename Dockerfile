# syntax=docker/dockerfile:1

FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (git for pip VCS, curl for healthchecks, libpq for psycopg)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq5 \
       curl \
       ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy project
COPY . .

# Ensure entrypoint is executable
RUN chmod +x /app/entrypoint.sh

# Default command is overridden per service in docker-compose
CMD ["python", "manage.py", "help"]


