#!/usr/bin/env bash
# Build Lights Out on a server over SSH and (re)start it. For maintainers who build from source on their own box;
# everyone else can use compose.yaml and the published image (see the README).
#
# Settings come from deploy.env next to this script (not committed; copy deploy.env.example):
#   HOST       ssh host to deploy to
#   REMOTE_DIR folder on that host for the source (default: lightsout)
#   DATA_DIR   folder on that host for the database and keys (default: ~/lightsout-data)
#   ENV_FILES  space-separated env files on that host with the app's settings (see .env.example)
set -euo pipefail
cd "$(dirname "$0")"
[ -f deploy.env ] && . ./deploy.env
: "${HOST:?set HOST in deploy.env}"
REMOTE_DIR=${REMOTE_DIR:-lightsout}
DATA_DIR=${DATA_DIR:-\$HOME/lightsout-data}
ENV_FILES=${ENV_FILES:-}
ENV_ARGS=""
for f in $ENV_FILES; do ENV_ARGS="$ENV_ARGS --env-file $f"; done

rsync -a --delete --exclude .venv --exclude .git --exclude /data --exclude __pycache__ --exclude .pytest_cache \
  --exclude .env --exclude deploy.env --exclude .cloudflare ./ "$HOST:$REMOTE_DIR/"

ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
[ -w "$DATA_DIR" ] || { sudo mkdir -p "$DATA_DIR" && sudo chown "\$(id -u):\$(id -g)" "$DATA_DIR"; }
cd "$REMOTE_DIR"
docker build -q -t lightsout:latest .
docker rm -f lightsout >/dev/null 2>&1 || true
docker run -d --name lightsout --restart unless-stopped \
  --user "\$(id -u):\$(id -g)" \
  -p 127.0.0.1:8787:8787 \
  -v "$DATA_DIR:/data" \
  $ENV_ARGS \
  lightsout:latest >/dev/null
docker image prune -f >/dev/null
echo "lightsout: \$(docker ps --filter name=lightsout --format '{{.Status}}')"
REMOTE
