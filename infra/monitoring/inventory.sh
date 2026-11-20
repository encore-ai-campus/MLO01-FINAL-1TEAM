#!/usr/bin/env bash
set -euo pipefail
hostname
cat /etc/os-release
ip -brief address
free -m
df -h /
systemctl list-units --type=service --all --no-pager --no-legend |
    awk 'tolower($1) ~ /prometheus|grafana|exporter|alertmanager|loki|alloy|promtail|fluent|rsyslog|nginx|apache|docker|podman|postgres|qdrant|minio|rag|pipeline/'
systemctl list-unit-files --type=service --no-pager --no-legend |
    awk 'tolower($1) ~ /prometheus|grafana|exporter|alertmanager|loki|alloy|promtail|fluent|rsyslog|nginx|apache|docker|podman|postgres|qdrant|minio|rag|pipeline/'
ss -lntu
if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
    docker ps -a --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'
fi
