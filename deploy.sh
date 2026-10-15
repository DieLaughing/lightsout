#!/usr/bin/env bash
# Build Lights Out on a server over SSH and (re)start it. For maintainers who build from source on their own box;
# everyone else can use compose.yaml and the published image (see the README).
#
# Settings come from deploy.env next to this script (not committed; copy deploy.env.example), or the file named by
# DEPLOY_ENV (e.g. DEPLOY_ENV=deploy.dev.env ./deploy.sh for a staging copy beside production):
#   HOST       ssh host to deploy to
#   REMOTE_DIR folder on that host for the source (default: lightsout)
#   DATA_DIR   folder on that host for the database and keys (default: ~/lightsout-data)
#   ENV_FILES  space-separated env files on that host with the app's settings (see .env.example)
#   COMPOSE_DIR  optional: folder on that host with a compose.yaml that defines a service called NAME. When set, the
#              container is (re)created with Docker Compose instead of docker run, and DATA_DIR, ENV_FILES and PORT
#              come from that compose file.
set -euo pipefail
cd "$(dirname "$0")"
DEPLOY_ENV=${DEPLOY_ENV:-deploy.env}
[ -f "$DEPLOY_ENV" ] && . "./$DEPLOY_ENV"
: "${HOST:?set HOST in deploy.env}"
NAME=${NAME:-lightsout}        # container name
IMAGE=${IMAGE:-lightsout:latest}
PORT=${PORT:-8787}             # port on the host's 127.0.0.1
REMOTE_DIR=${REMOTE_DIR:-lightsout}
DATA_DIR=${DATA_DIR:-\$HOME/lightsout-data}
ENV_FILES=${ENV_FILES:-}
COMPOSE_DIR=${COMPOSE_DIR:-}
ENV_ARGS=""
for f in $ENV_FILES; do ENV_ARGS="$ENV_ARGS --env-file $f"; done

rsync -a --delete --exclude .venv --exclude .git --exclude /data --exclude __pycache__ --exclude .pytest_cache \
  --exclude .env --exclude 'deploy*.env' --exclude CLAUDE.local.md --exclude .cloudflare ./ "$HOST:$REMOTE_DIR/"

ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
[ -w "$DATA_DIR" ] || { sudo mkdir -p "$DATA_DIR" && sudo chown "\$(id -u):\$(id -g)" "$DATA_DIR"; }
cd "$REMOTE_DIR"
docker build -q -t $IMAGE .
if [ -n "$COMPOSE_DIR" ]; then
  docker compose --project-directory "$COMPOSE_DIR" up -d --no-deps --force-recreate $NAME
else
  docker rm -f $NAME >/dev/null 2>&1 || true
  docker run -d --name $NAME --restart unless-stopped \
    --user "\$(id -u):\$(id -g)" \
    -p 127.0.0.1:$PORT:8787 \
    -v "$DATA_DIR:/data" \
    $ENV_ARGS \
    $IMAGE >/dev/null
fi
docker image prune -f >/dev/null
echo "$NAME: \$(docker ps --filter name=^$NAME\$ --format '{{.Status}}')"
REMOTE
