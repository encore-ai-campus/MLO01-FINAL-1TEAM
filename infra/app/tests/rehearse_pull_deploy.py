"""Exercise real Compose, DB preflight and HTTP rollback on disposable runners."""
import importlib.util
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("pull_deploy", root / "pull-deploy.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

app = Path(sys.argv[1])
state = app / "state"
state.mkdir()
env_before = (app / ".env").read_bytes()
runtime = cd.Runtime(app)
# Hosted runners can install the Compose plugin in the runner's home.
runtime.env["HOME"] = str(Path.home())
subprocess.run(["docker", "compose", "version"], env=runtime.env, check=True)
source = cd.Deployment(runtime, state)
initial = runtime.preserve()
runtime.verify(initial)

def image(ref, token):
    return {"ref": ref, "image_id": runtime.image_id(ref), "sha": token * 40,
            "digest": "sha256:" + token * 64, "run": 1}

good = image("rag-api:cd-good", "a")
bad = image("rag-api:cd-broken", "b")
incompatible = image("rag-api:cd-good", "c")

def apply(deployment, candidate):
    approved = {k: candidate[k] for k in ("sha", "digest")}
    with patch.object(runtime, "candidate", return_value=candidate):
        deployment.run("deploy", approved)

apply(source, good)
assert runtime.live()[0] == good["image_id"]
print("PASS: real image switch, DB preflight and HTTP verification", flush=True)

wait = cd.wait_healthy
with patch.object(cd, "wait_healthy", side_effect=lambda check: wait(check, timeout=8)):
    try:
        apply(source, bad)
    except RuntimeError as error:
        assert "did not stabilize" in str(error), error
    else:
        raise AssertionError("broken HTTP image unexpectedly succeeded")
assert source.state["failed"][bad["digest"]] == bad["sha"]
assert runtime.live()[0] == good["image_id"]
runtime.verify(good)
print("PASS: real HTTP failure rolled back to the previous healthy image", flush=True)

sql = ["docker", "exec", "--user", "postgres", "rag-cd-db", "psql",
       "-X", "-U", "postgres", "-d", "rag_dev", "-v", "ON_ERROR_STOP=1", "-c"]
subprocess.run(sql + ["ALTER TABLE documents RENAME COLUMN allowed_depts TO isolated_missing"], check=True)
try:
    try:
        apply(source, incompatible)
    except RuntimeError:
        assert source.state["failed"][incompatible["digest"]] == incompatible["sha"]
    else:
        raise AssertionError("incompatible schema did not block the deployment")
    assert runtime.live()[0] == good["image_id"]
finally:
    subprocess.run(sql + ["ALTER TABLE documents RENAME COLUMN isolated_missing TO allowed_depts"], check=True)
assert (app / ".env").read_bytes() == env_before
print("PASS: incompatible DB schema blocked the switch and .env stayed byte-identical", flush=True)
