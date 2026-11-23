"""Build MON01 configuration from tracked inventory and site-local overrides."""
import argparse
from hashlib import sha256
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PROBES = {"http": "http_health", "tcp": "tcp_connect", "dns": "site_dns"}
KEEP = {
    "linux": r"node_cpu_seconds_total|node_memory_(MemTotal|MemAvailable)_bytes|node_filesystem_(size_bytes|avail_bytes|device_error)|node_network_(receive|transmit)_bytes_total|node_boot_time_seconds|process_resident_memory_bytes",
    "windows": r"windows_cpu_time_seconds_total|windows_memory_physical_(total|free)_bytes|windows_logical_disk_(size|free)_bytes|windows_net_bytes_(received|sent)_total|windows_system_boot_time_timestamp|windows_service_state",
    "fw-system": r"node_boot_time_seconds|node_load1|node_cpu_seconds_total",
    "snmp": r"ifOperStatus|ifHCInOctets|ifHCOutOctets|ifInErrors|ifOutErrors",
    "app": r"http_requests_total|http_request_duration_seconds_(bucket|count|sum)|pipeline_documents_total|pipeline_last_success_timestamp_seconds",
    "postgres": r"pg_up|pg_stat_database_numbackends",
    "minio": r"minio_cluster_usage_objects_(total_bytes|count)",
}
SELF_METRICS = r"prometheus_tsdb_head_series|prometheus_tsdb_storage_blocks_bytes|process_(resident_memory_bytes|start_time_seconds)"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(directory, name, data):
    path = directory / name
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def endpoint(value, http=False):
    if not isinstance(value, str) or not value or re.search(r"\s", value):
        raise ValueError("target must be a nonempty address without whitespace")
    parsed = urlsplit(value if http else "//" + value)
    if not parsed.hostname or not re.fullmatch(r"[A-Za-z0-9._-]+|[0-9a-fA-F:]+", parsed.hostname):
        raise ValueError("invalid target host")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ValueError("credentials, query parameters and fragments are forbidden in targets")
    if http:
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("expected an HTTP(S) URL")
    elif parsed.path or not parsed.port:
        raise ValueError("expected host:port")
    if parsed.port == 0:
        raise ValueError("port must be nonzero")
    return value


def labels(entry):
    result = {"server": entry["server"], "check": entry["check"]}
    if entry["target"] is not None:
        result["target_id"] = sha256(entry["target"].encode()).hexdigest()[:16]
    return result


def expected(entries):
    # These constants encode explicit configuration intent only.
    return [{
        "record": "monitoring:expected",
        "labels": labels(entry),
        "expr": f'vector({int(entry["target"] is not None)})',
    } for entry in entries]


def keep(pattern):
    return {"source_labels": ["__name__"], "regex": pattern, "action": "keep"}


def scrape(job):
    config = {
        "job_name": job,
        "file_sd_configs": [{"files": [f"targets/{job}.json"], "refresh_interval": "30s"}],
        "sample_limit": 1200 if job == "windows" else 700,
        "target_limit": 20,
        "body_size_limit": "4MB",
        "label_limit": 24,
    }
    if job in PROBES or job == "snmp":
        exporter = "127.0.0.1:9116" if job == "snmp" else "127.0.0.1:9115"
        config["metrics_path"] = "/snmp" if job == "snmp" else "/probe"
        config["params"] = ({"module": ["if_mib"], "auth": ["fw01"]} if job == "snmp"
                            else {"module": [PROBES[job]]})
        config["relabel_configs"] = [
            {"source_labels": ["__address__"], "target_label": "__param_target"},
            {"source_labels": ["__address__"], "target_label": "instance"},
            {"target_label": "__address__", "replacement": exporter},
        ]
    if job == "snmp":
        config.update(scrape_interval="120s", scrape_timeout="15s")
    if job == "internal":
        config["metric_relabel_configs"] = [keep(SELF_METRICS)]
    if job in KEEP:
        config["metric_relabel_configs"] = [keep(KEEP[job])]
    if job == "windows":
        config["metric_relabel_configs"].append({
            "source_labels": ["__name__", "name", "state"],
            "regex": r"(windows_service_state;(?i:NTDS|DNS|Netlogon|Kdc);running|[^;]+;;)",
            "action": "keep",
        })
    if job in {"app", "minio", "postgres", "linux", "windows", "fw-system"}:
        config["relabel_configs"] = [
            {"source_labels": ["site_scheme"], "target_label": "__scheme__"},
            {"source_labels": ["site_path"], "target_label": "__metrics_path__"},
            {"regex": "site_scheme|site_path", "action": "labeldrop"},
        ]
    if job == "minio":
        config["authorization"] = {"credentials_file": "/run/site-secrets/minio-token"}
        config["scrape_interval"] = "120s"
    return config


def target_group(entry):
    target = entry["target"]
    target_labels = labels(entry)
    if entry["job"] in {"app", "minio", "postgres", "linux", "windows", "fw-system"}:
        url = urlsplit(target if "://" in target else "http://" + target)
        target = url.netloc
        target_labels.update(site_scheme=url.scheme, site_path=url.path or "/metrics")
    return {"targets": [target], "labels": target_labels}


def render(output, site):
    defaults = read_json(ROOT / "inventory.json")
    entries = defaults["checks"]
    overrides = site.get("targets", {})
    known = {entry["check"] for entry in entries}
    if set(overrides) - known or set(site) - {"targets", "dns"}:
        raise ValueError("unknown site setting or check")
    for entry in entries:
        job = entry["job"]
        target = overrides.get(entry["check"], entry["target"])
        if target is not None:
            target = endpoint(target, job == "http" or (
                job not in {"tcp", "dns", "snmp", "internal"} and isinstance(target, str) and "://" in target
            ))
        entry["target"] = target

    modules = {
        "http_health": {"prober": "http", "timeout": "5s",
                        "http": {"preferred_ip_protocol": "ip4", "follow_redirects": False, "body_size_limit": "1MB"}},
        "tcp_connect": {"prober": "tcp", "timeout": "5s",
                        "tcp": {"preferred_ip_protocol": "ip4"}},
    }
    if any(entry["target"] and entry["job"] == "dns" for entry in entries):
        dns = defaults["dns"] | site.get("dns", {})
        name = dns["name"]
        pattern = dns["answer_pattern"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.", name) or not pattern:
            raise ValueError("DNS requires an absolute query name and an expected answer pattern")
        if dns["type"] not in {"A", "AAAA", "SRV", "CNAME"}:
            raise ValueError("unsupported DNS record type")
        modules["site_dns"] = {
            "prober": "dns", "timeout": "5s",
            "dns": {
                "preferred_ip_protocol": "ip4",
                "transport_protocol": "udp",
                "query_name": name,
                "query_type": dns["type"],
                "recursion_desired": False,
                "valid_rcodes": ["NOERROR"],
                "validate_answer_rrs": {"fail_if_none_matches_regexp": [pattern]},
            },
        }

    (output / "targets").mkdir(parents=True, exist_ok=True)
    jobs = []
    for job in sorted({entry["job"] for entry in entries}):
        active = [entry for entry in entries if entry["job"] == job and entry["target"]]
        write_json(output / "targets", f"{job}.json", [target_group(entry) for entry in active])
        if active:
            jobs.append(scrape(job))
    config = {
        "global": {"scrape_interval": "60s", "scrape_timeout": "8s", "evaluation_interval": "30s"},
        "rule_files": ["expected.yml", "rules.yml"],
        "alerting": {"alertmanagers": [{"static_configs": [{"targets": ["127.0.0.1:9093"]}]}]},
        "scrape_configs": jobs,
    }
    write_json(output, "prometheus.yml", config)
    write_json(output, "expected.yml", {"groups": [{"name": "inventory", "rules": expected(entries)}]})
    write_json(output, "blackbox.yml", {"modules": modules})
    shutil.copyfile(ROOT / "rules.yml", output / "rules.yml")

    if (ROOT / "secrets" / "alert-webhook-url").is_file():
        write_json(output, "alertmanager.yml", {
            "route": {
                "receiver": "webhook", "group_by": ["alertname", "server"],
                "group_wait": "30s", "group_interval": "5m", "repeat_interval": "4h",
            },
            "receivers": [{
                "name": "webhook", "webhook_configs": [{
                    "url_file": "/run/site-secrets/alert-webhook-url", "send_resolved": True,
                }],
            }],
        })
    else:
        shutil.copyfile(ROOT / "alertmanager.yml", output / "alertmanager.yml")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=ROOT / "site.json")
    parser.add_argument("--output", type=Path, default=ROOT / "runtime")
    args = parser.parse_args()
    render(args.output, read_json(args.site) if args.site.is_file() else {})
