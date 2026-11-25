#!/usr/bin/env bash
set -euo pipefail
image=${1:?image reference required}
fixture=$(mktemp -d)
trap 'rm -f -- "$fixture/compose.yml" "$fixture/.env"; rmdir -- "$fixture"' EXIT
cp infra/app/compose.yml "$fixture/compose.yml"
cp infra/app/.env.example "$fixture/.env"
docker compose --project-directory "$fixture" -f "$fixture/compose.yml" config --quiet
docker run --rm --network none "$image" python -c 'from rag_api.main import app; print(app.title)'
python3 infra/app/verify_ui_image.py "$image"
