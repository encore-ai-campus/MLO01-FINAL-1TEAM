"""Loopback checks for the real RAG service without test data writes."""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
SEARCH = "/api/v1/search/documents"
QA = "/api/v1/qa/answers"


def request(path, data=None):
    payload = json.dumps(data).encode() if data is not None else None
    req = Request(BASE + path, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=5) as response:
            return response.status, response.headers, json.load(response)
    except HTTPError as error:
        return error.code, error.headers, None


def smoke():
    status, _, spec = request("/openapi.json")
    if status != 200 or not {SEARCH, QA} <= spec.get("paths", {}).keys():
        raise RuntimeError("OpenAPI verification failed")
    status, _, result = request(SEARCH, {"query": "출장비"})
    if status != 200 or not isinstance(result.get("total"), int):
        raise RuntimeError("Search verification failed")
    search_state = result.get("status", {})
    if search_state.get("degraded") and search_state.get("reason_codes") != ["EMBEDDING_UNAVAILABLE"]:
        raise RuntimeError("Search reported degraded")
    status, headers, _ = request(QA, {"question": "출장비 정산 방법"})
    if status != 401 or not headers.get("WWW-Authenticate", "").lower().startswith("bearer"):
        raise RuntimeError("Anonymous QA verification failed")


def wait_healthy(check, *, timeout=90):
    """Require two consecutive passes so transient startup success cannot promote an image."""
    end = time.monotonic() + timeout
    successes = 0
    while time.monotonic() < end:
        try:
            check()
            successes += 1
            if successes == 2:
                return
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError, URLError):
            successes = 0
        time.sleep(2)
    raise RuntimeError("Container or API smoke checks did not stabilize")
