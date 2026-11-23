"""Exercise Prometheus, Blackbox and the Grafana queries using a local HTTP fixture."""
import argparse
from base64 import b64encode
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from secrets import token_urlsafe
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from render import render

HTTP = build_opener(ProxyHandler({}))


class HealthHandler(BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):
        pass


def local_address():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{sock.getsockname()[1]}"


def get(address, path, as_json=False):
    with HTTP.open(f"http://{address}{path}", timeout=3) as response:
        return json.load(response) if as_json else response.status


def query(address, expression):
    return get(address, "/api/v1/query?" + urlencode({"query": expression}), True)["data"]["result"]


def wait_for(predicate):
    deadline = monotonic() + 25
    while monotonic() < deadline:
        try:
            if predicate():
                return
        except URLError:
            pass
        sleep(0.25)
    raise RuntimeError("Timed out waiting for the expected monitoring state")


def start(stack, log, *command, env=None):
    output = stack.enter_context(log.open("wb"))
    process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT, env=env)

    def stop():
        process.terminate()
        process.wait(timeout=10)

    stack.callback(stop)


def verify_grafana(stack, work, grafana, prometheus):
    address = local_address()
    directory = work / "provisioning"
    shutil.copytree(ROOT / "grafana" / "provisioning", directory)
    dashboards = directory / "dashboards" / "infra.yml"
    dashboards.write_text(dashboards.read_text().replace(
        "/opt/infra-dashboards", json.dumps((ROOT / "grafana" / "dashboards").as_posix())
    ))
    datasource = directory / "datasources" / "prometheus.yml"
    datasource.write_text(datasource.read_text().replace("127.0.0.1:9090", prometheus))

    for name in ("grafana-data", "grafana-logs", "grafana-plugins"):
        (work / name).mkdir()
    password = token_urlsafe()
    env = os.environ | {
        "GF_SERVER_HTTP_ADDR": "127.0.0.1",
        "GF_SERVER_HTTP_PORT": address.split(":")[1],
        "GF_SECURITY_ADMIN_PASSWORD": password,
        "GF_PATHS_DATA": str(work / "grafana-data"),
        "GF_PATHS_LOGS": str(work / "grafana-logs"),
        "GF_PATHS_PLUGINS": str(work / "grafana-plugins"),
        "GF_PATHS_PROVISIONING": str(directory),
        "GF_ANALYTICS_REPORTING_ENABLED": "false",
        "GF_ANALYTICS_CHECK_FOR_UPDATES": "false",
        "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES": "false",
        "GF_PLUGINS_PREINSTALL_DISABLED": "true",
    }
    home = Path(grafana).resolve().parents[1]
    log = work / "grafana.log"
    start(stack, log, grafana, "server", f"--homepath={home}", env=env)
    try:
        wait_for(lambda: get(address, "/api/health", True).get("database") == "ok")
    except RuntimeError as error:
        tail = log.read_text(encoding="utf-8", errors="replace").replace(password, "[redacted]")
        raise RuntimeError(f"Grafana did not become ready:\n{tail[-4000:]}") from error
    auth = b64encode(f"admin:{password}".encode()).decode()

    def grafana_get(path):
        request = Request(f"http://{address}{path}", headers={"Authorization": f"Basic {auth}"})
        with HTTP.open(request, timeout=5) as response:
            return json.load(response)

    expected = {file.stem for file in (ROOT / "grafana" / "dashboards").glob("*.json")}
    wait_for(lambda: {dashboard["uid"] for dashboard in grafana_get("/api/search")} >= expected)
    expression = 'monitoring:check_state{check="web-http"}'
    path = "/api/datasources/proxy/uid/infra-prometheus/api/v1/query?"
    values = grafana_get(path + urlencode({"query": expression}))["data"]["result"]
    if not values or float(values[0]["value"][1]) != 1:
        raise RuntimeError("Grafana datasource did not return the successful HTTP probe")
    print("PASS: Grafana provisioned all six dashboards and returned actual probe data")


def smoke(prometheus, blackbox, grafana=None):
    with TemporaryDirectory(prefix="infra-monitoring-") as tmp, ExitStack() as stack:
        work = Path(tmp)
        fixture = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler))
        Thread(target=fixture.serve_forever, daemon=True).start()
        stack.callback(fixture.shutdown)
        target = f"http://127.0.0.1:{fixture.server_port}/health"
        prom, bb = local_address(), local_address()
        render(work, {"targets": {
            "web-http": target, "grafana": None, "alertmanager": None,
            "prometheus": prom, "blackbox": bb,
        }})

        # Accelerate the timing only for this isolated suite.
        config_path = work / "prometheus.yml"
        config = json.loads(config_path.read_text())
        config["global"] = {"scrape_interval": "1s", "scrape_timeout": "900ms", "evaluation_interval": "1s"}
        config.pop("alerting")
        for job in config["scrape_configs"]:
            for action in job.get("relabel_configs", []):
                if action.get("replacement") == "127.0.0.1:9115":
                    action["replacement"] = bb
        config_path.write_text(json.dumps(config))
        path = work / "rules.yml"
        rules = json.loads(path.read_text(encoding="utf-8"))
        for rule in rules["groups"][1]["rules"]:
            rule["for"] = "2s"
        path.write_text(json.dumps(rules))

        start(stack, work / "blackbox.log", blackbox, f"--config.file={work / 'blackbox.yml'}",
              f"--web.listen-address={bb}")
        wait_for(lambda: get(bb, "/-/healthy") == 200)
        start(stack, work / "prometheus.log", prometheus, f"--config.file={config_path}",
              f"--web.listen-address={prom}", f"--storage.tsdb.path={work / 'tsdb'}")

        def state(check, value):
            return any(float(row["value"][1]) == value
                       for row in query(prom, 'monitoring:check_state{check="' + check + '"}'))

        def firing():
            return query(prom, 'ALERTS{alertname="WebOrApiUnavailable",check="web-http",alertstate="firing"}')

        wait_for(lambda: state("web-http", 1) and state("web-system", -2))
        print("PASS: actual HTTP 200 is UP and an unconfigured check stays unconfigured")

        if grafana:
            verify_grafana(stack, work, grafana, prom)

        HealthHandler.status = 503
        wait_for(lambda: state("web-http", 0) and firing())
        print("PASS: actual HTTP 503 is DOWN and raises WebOrApiUnavailable")

        HealthHandler.status = 200
        wait_for(lambda: state("web-http", 1) and not firing())
        print("PASS: HTTP recovery returns to UP and clears the alert")

        fixture.shutdown()
        wait_for(lambda: state("web-http", 0))
        print("PASS: a stopped HTTP fixture is DOWN")

        count = 0
        for file in sorted((ROOT / "grafana" / "dashboards").glob("*.json")):
            for panel in json.loads(file.read_text(encoding="utf-8"))["panels"]:
                for series in panel["targets"]:
                    query(prom, series["expr"].replace("${server:regex}", ".+"))
                    count += 1
        print(f"PASS: Prometheus evaluated {count} queries from six Grafana dashboards")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prometheus", help="Path to a trusted Prometheus binary")
    parser.add_argument("blackbox", help="Path to a trusted Blackbox Exporter binary")
    parser.add_argument("--grafana", help="Path to a trusted Grafana binary")
    args = parser.parse_args()
    smoke(args.prometheus, args.blackbox, args.grafana)
