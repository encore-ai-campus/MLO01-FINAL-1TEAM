#!/usr/bin/env bash
# Runs on an isolated GitHub-hosted runner only.
set -euo pipefail
work=$(mktemp -d)
cp infra/app/compose.yml "$work/compose.yml"
cat > "$work/.env" <<'ENV'
RAG_IMAGE=rag-api:ci
DATABASE_URL=postgresql://rag_app:throwaway-fixture@rag-cd-db:5432/rag_dev
ACTIVE_PACKS=base,it_service
DB_POOL_SIZE=2
VECTOR_CHANNEL_ENABLED=false
ENV
compose=(docker compose -p app --project-directory "$work" -f "$work/compose.yml")
cleanup() {
    docker rm -f rag-cd-db >/dev/null 2>&1 || true
    "${compose[@]}" down >/dev/null 2>&1 || true
}
trap cleanup EXIT
"${compose[@]}" create rag-api
db_image=$(awk '$1 == "image:" {print $2}' infra/database/compose.yml)
docker run -d --name rag-cd-db --network app_default --shm-size 512m \
    -e POSTGRES_PASSWORD=disposable -e POSTGRES_DB=rag_dev "$db_image" \
    -c shared_preload_libraries=timescaledb,pg_textsearch
for attempt in $(seq 1 60); do
    if docker exec rag-cd-db pg_isready -h 127.0.0.1 -U postgres; then break; fi
    test "$attempt" != 60
    sleep 2
done
db() { docker exec -i --user postgres rag-cd-db psql -X -U postgres -d rag_dev -v ON_ERROR_STOP=1 "$@"; }
db < db/init.sql
db -c "CREATE ROLE rag_app LOGIN PASSWORD 'throwaway-fixture'" \
   -c 'GRANT SELECT ON departments, ontology_concepts, documents, document_versions, chunks TO rag_app'
"${compose[@]}" up -d --no-build --pull never rag-api
docker build -t rag-api:cd-good - <<'DOCKER'
FROM rag-api:ci
LABEL org.opencontainers.image.revision="isolated-good"
DOCKER
docker build -t rag-api:cd-broken - <<'DOCKER'
FROM rag-api:ci
CMD ["python", "-c", "import time; time.sleep(180)"]
DOCKER
python3 infra/app/tests/rehearse_pull_deploy.py "$work"
