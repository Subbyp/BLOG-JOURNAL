#!/usr/bin/env bash
# Start the blog on http://127.0.0.1:5000 using the project virtualenv.
#
#   ./run.sh          start the development server
#
# Set SECRET_KEY in your shell (or a .env file) to keep sessions valid across
# restarts. Without it a fresh random key is generated each time, which logs you
# out whenever the process restarts. That is fine locally, not in production.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "No virtualenv found. Create one first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ -z "${SECRET_KEY:-}" ]; then
  export SECRET_KEY="$(
    .venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
  )"
  echo "SECRET_KEY not set, using a temporary one (sessions reset on restart)."
fi

if [ ! -f instance/blog.db ]; then
  echo "No database yet. Creating one and an admin user."
  echo "  admin password: ${ADMIN_PASSWORD:-admin123}"
  .venv/bin/python -m flask --app app init-db
fi

echo
echo "  site      http://127.0.0.1:5000"
echo "  dashboard http://127.0.0.1:5000/admin"
echo "  stop      Ctrl+C"
echo

.venv/bin/python -m flask --app app run
