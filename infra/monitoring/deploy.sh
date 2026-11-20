#!/usr/bin/env bash
set -euo pipefail
umask 022
cd -- "$(dirname -- "$0")"
if [[ $(uname -s) != Linux ]]; then
    echo "Run deployment on MON01 Linux." >&2
    exit 2
fi

docker compose version
docker info >/dev/null
if [[ ! -s secrets/grafana-admin ]]; then
    echo "Create the Grafana bootstrap password file first. See README.md." >&2
    exit 2
fi

stage=$(mktemp -d .runtime-stage.XXXXXX)
trap 'rm -f -- "$stage"/*.yml "$stage"/targets/*.json; rmdir -- "$stage"/targets "$stage" 2>/dev/null || true' EXIT
chmod 755 "$stage"
python3 render.py --output "$stage"
export MON_RUNTIME=$stage

services=(prometheus grafana blackbox alertmanager)
ports=(9090 3000 9115 9093)
if [[ $(cat "$stage/targets/snmp.json") != '[]' ]]; then
    [[ -s secrets/snmp.yml ]] || { echo "Missing SNMPv3 config" >&2; exit 2; }
    export COMPOSE_PROFILES=snmp
    services+=(snmp)
    ports+=(9116)
fi
if [[ $(cat "$stage/targets/minio.json") != '[]' && ! -s secrets/minio-token ]]; then
    echo "Missing MinIO metrics token" >&2
    exit 2
fi
for i in "${!ports[@]}"; do
    if [[ -n $(ss -H -ltn "sport = :${ports[i]}") ]] &&
       [[ -z $(docker compose ps --status running -q "${services[i]}") ]]; then
        echo "Port ${ports[i]} has an existing listener. Audit and reuse it before proceeding." >&2
        exit 1
    fi
done

docker compose config -q
docker compose run --pull missing --rm --no-deps --entrypoint /bin/promtool prometheus check config /etc/monitoring/prometheus.yml
docker compose run --pull missing --rm --no-deps blackbox --config.file=/etc/monitoring/blackbox.yml --config.check
docker compose run --pull missing --rm --no-deps --entrypoint /bin/amtool alertmanager check-config /etc/monitoring/alertmanager.yml

# Versioned runtime directories allow manual rollback, including when recreation fails.
unset MON_RUNTIME
mkdir -p runtime
revision=$(mktemp -d runtime/release.XXXXXX)
chmod 755 "$revision"
mv -- "$stage"/*.yml "$stage"/targets "$revision/"
export MON_RUNTIME=$revision
docker compose up -d "${services[@]}"
printf 'Active config directory: %s\n' "$revision"
printf 'Verify with: python3 verify.py --runtime %s\n' "$revision"
