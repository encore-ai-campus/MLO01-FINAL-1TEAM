"""Verify current local Prometheus, Grafana, Alertmanager and discovered targets."""
import argparse
import json
import sys
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

HTTP = build_opener(ProxyHandler({}))
PROM = "http://127.0.0.1:9090"


def get(url, as_json=False):
    with HTTP.open(url, timeout=8) as response:
        return json.load(response) if as_json else response.status


def run(runtime):
    get(PROM + "/-/ready")
    get("http://127.0.0.1:9093/-/ready")
    grafana = get("http://127.0.0.1:3000/api/health", True)
    if grafana.get("database") != "ok":
        raise RuntimeError("Grafana database health check failed")

    wanted = {}
    for path in sorted((runtime / "targets").glob("*.json")):
        for group in json.loads(path.read_text(encoding="utf-8")):
            wanted[group["labels"]["check"]] = path.stem
    inventory = json.loads((runtime / "expected.yml").read_text(encoding="utf-8"))
    all_checks = {rule["labels"]["check"] for group in inventory["groups"] for rule in group["rules"]}
    if not all_checks or not wanted:
        raise RuntimeError("Runtime inventory or active targets absent")
    discovered = get(PROM + "/api/v1/targets?state=active", True)["data"]["activeTargets"]
    by_check = {entry["labels"].get("check"): entry for entry in discovered}
    data = get(PROM + "/api/v1/query?query=monitoring%3Acheck_state", True)["data"]["result"]
    states = {row["metric"]["check"]: int(float(row["value"][1])) for row in data}
    failures = set()
    for name in sorted(wanted):
        target = by_check.get(name)
        scrape = target["health"] if target else "missing"
        value = states.get(name, -1)
        state = {1: "UP", 0: "DOWN", -1: "NO DATA", -2: "NOT CONFIGURED"}.get(value, str(value))
        print(f"{name:24} scrape={scrape:8} service={state}")
        if scrape != "up" or value != 1:
            failures.add(name)
    for name in sorted(all_checks - set(wanted)):
        if states.get(name) == -2:
            print(f"{name:24} NOT CONFIGURED")
        else:
            print(f"{name:24} Unexpected or missing status for inactive check")
            failures.add(name)
    extras = set(by_check) - set(wanted)
    if extras:
        print("Unexpected targets:", ", ".join(sorted(str(name) for name in extras)))
    return 1 if failures or extras else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    args = parser.parse_args()
    try:
        sys.exit(run(args.runtime))
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        sys.exit(f"Verification failed: {error}")
