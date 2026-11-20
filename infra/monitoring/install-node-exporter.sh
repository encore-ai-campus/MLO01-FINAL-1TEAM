#!/usr/bin/env bash
set -euo pipefail
if [[ $# != 1 || $EUID != 0 ]]; then
    echo "Usage: sudo bash install-node-exporter.sh <approved-local-IPv4>" >&2
    exit 2
fi
bind_ip=$1
if [[ $bind_ip != 127.0.0.1 ]] && ! ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}' | grep -Fx "$bind_ip" >/dev/null; then
    echo "Address is not a local IPv4 address" >&2
    exit 2
fi
if [[ -e /etc/default/prometheus-node-exporter ]] ||
   command -v prometheus-node-exporter >/dev/null ||
   command -v node_exporter >/dev/null ||
   systemctl cat prometheus-node-exporter.service >/dev/null 2>&1 ||
   systemctl cat node_exporter.service >/dev/null 2>&1 ||
   [[ -n $(ss -H -ltn 'sport = :9100') ]]; then
    echo "Existing exporter or listener detected. Review and reuse it." >&2
    exit 1
fi
if command -v docker >/dev/null && docker info >/dev/null 2>&1 &&
   [[ $(docker ps -a --format '{{.Image}}' | grep -cE 'node[-_]exporter' || true) != 0 ]]; then
    echo "Existing exporter container detected. Review and reuse it." >&2
    exit 1
fi
# The packaged service inherits ARGS from its EnvironmentFile.
install -m 0644 /dev/null /etc/default/prometheus-node-exporter
printf 'ARGS="--web.listen-address=%s:9100 --collector.disable-defaults --collector.cpu --collector.meminfo --collector.filesystem --collector.netdev --collector.stat"\n' "$bind_ip" \
    > /etc/default/prometheus-node-exporter
DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Options::=--force-confold install --no-install-recommends -y prometheus-node-exporter
systemctl enable --now prometheus-node-exporter
curl --noproxy '*' --fail --silent --show-error --max-time 5 "http://$bind_ip:9100/metrics" |
    awk '/^node_boot_time_seconds / {print; seen=1} END {exit !seen}'
