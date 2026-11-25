#!/usr/bin/env bash
set -euo pipefail
umask 077

DEPLOY_DIR=/opt/database
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export DB_BIND_IP="${1:?DB_BIND_IP required}"

if [[ $EUID -ne 0 ]]; then
    echo "Run this script with sudo."
    exit 1
fi

echo "[1/6] Preflight checks"

test -s "$DEPLOY_DIR/.env"
test -s "$DEPLOY_DIR/compose.yml"
test -s "$SOURCE_DIR/compose.yml"

docker volume inspect rag-postgres-data >/dev/null
docker inspect rag-postgres >/dev/null
systemctl is-active --quiet rag-db-firewall.service

PROJECT="$(docker inspect \
    -f '{{ index .Config.Labels "com.docker.compose.project" }}' \
    rag-postgres)"

test -n "$PROJECT"

echo "[2/6] Validate configuration"

compose=(
    docker compose
    --project-directory "$DEPLOY_DIR"
    --project-name "$PROJECT"
    --env-file "$DEPLOY_DIR/.env"
)

"${compose[@]}" -f "$SOURCE_DIR/compose.yml" config --quiet

echo "[3/6] Backup existing configuration"

BACKUP="$(mktemp -d "$DEPLOY_DIR/backup.XXXXXXXX")"

cp -p "$DEPLOY_DIR/compose.yml" "$BACKUP/"
cp -p "$DEPLOY_DIR/.env" "$BACKUP/"

echo "Backup: $BACKUP"

echo "[4/6] Install configuration"

if grep -q '^DB_BIND_IP=' "$DEPLOY_DIR/.env"; then
    grep -Fxq "DB_BIND_IP=$DB_BIND_IP" "$DEPLOY_DIR/.env" || {
        echo "Existing DB_BIND_IP differs. Aborting."
        exit 1
    }
else
    printf '\nDB_BIND_IP=%s\n' "$DB_BIND_IP" >> "$DEPLOY_DIR/.env"
fi

install -m 0644 \
    "$SOURCE_DIR/compose.yml" \
    "$DEPLOY_DIR/compose.yml"

echo "[5/6] Deploy PostgreSQL"

"${compose[@]}" -f "$DEPLOY_DIR/compose.yml" \
    up -d --no-recreate --wait --wait-timeout 120

echo "[6/6] Health check"

docker exec rag-postgres \
    pg_isready -U postgres -d rag_dev

docker exec -u postgres rag-postgres \
    psql -X -v ON_ERROR_STOP=1 -d rag_dev \
    -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"

echo "Database deployment successful."
