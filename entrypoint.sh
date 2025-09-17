#!/bin/sh
set -e

# Wait for Postgres if configured
if [ -n "$POSTGRES_HOST" ]; then
  until python - <<'PY'
import sys, socket, os
h=os.environ.get('POSTGRES_HOST','localhost');p=int(os.environ.get('POSTGRES_PORT','5432'))
s=socket.socket();
try:
    s.settimeout(2)
    s.connect((h,p))
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
  do
    echo "Waiting for Postgres at $POSTGRES_HOST:$POSTGRES_PORT..."
    sleep 1
  done
fi

# Run migrations
python manage.py migrate --noinput

# Collect static (noop if not configured)
python manage.py collectstatic --noinput || true

# Run command passed to container
exec "$@"


