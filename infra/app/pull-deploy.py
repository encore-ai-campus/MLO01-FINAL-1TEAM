#!/usr/bin/env python3
"""Apply an explicitly approved image to APP01 using the installed Compose project."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from smoke_test import smoke, wait_healthy

REPO = "encore-ai-campus/MLO01-FINAL-1TEAM"
IMAGE = "ghcr.io/encore-ai-campus/mlo01-final-1team/rag-api"
WORKFLOW = ".github/workflows/rag-ci.yml"
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
APP = Path("/opt/app")
STATE_DIR = APP / "cd-state"
APPROVAL = APP / "cd" / "approved.json"


@contextmanager
def locked(path):
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_state(path):
    return json.loads(path.read_text()) if path.exists() else {}


def write_state(path, state):
    temp = path.with_suffix(".tmp")
    with temp.open("w") as handle:
        json.dump(state, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    sync_dir(path.parent)


def approval(path):
    if not path.exists():
        return None
    info = path.lstat()
    if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
        raise RuntimeError("Approval file must be a root-owned, non-writable-by-others regular file")
    data = json.loads(path.read_text())
    if (set(data) != {"sha", "digest"} or not SHA.fullmatch(data["sha"])
            or not DIGEST.fullmatch(data["digest"])):
        raise RuntimeError("Approval must contain a full Git SHA and OCI sha256 digest")
    return data


def command(args, *, env=None, stdin=None, timeout=90, capture=False):
    # Avoid returning raw errors or printing expanded Compose configuration.
    done = subprocess.run(args, env=env, input=stdin,
                          stdin=subprocess.DEVNULL if stdin is None else None, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    if done.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed ({done.returncode})")
    return done.stdout.strip() if capture else None


def github_json(path):
    url = f"https://api.github.com/repos/{REPO}/{path}"
    with urlopen(Request(url, headers={"Accept": "application/vnd.github+json"}), timeout=15) as response:
        return json.load(response)


def successful_ci(sha):
    query = urlencode({"branch": "dev", "event": "push", "head_sha": sha, "per_page": 30})
    runs = github_json(f"actions/workflows/rag-ci.yml/runs?{query}")["workflow_runs"]
    for run in runs:
        if (run["head_sha"] == sha and run["head_branch"] == "dev" and run["event"] == "push"
                and run["head_repository"]["full_name"].lower() == REPO.lower()
                and run["path"].split("@")[0] == WORKFLOW):
            if run["status"] != "completed" or run["conclusion"] != "success":
                break
            jobs = github_json(f"actions/runs/{run['id']}/jobs?filter=latest")["jobs"]
            if any(job["name"] == "Publish tested RAG image" and job["conclusion"] == "success"
                   for job in jobs):
                return run["id"]
            break
    raise RuntimeError("No successful dev push RAG CI and image publication for the approved SHA")


class Runtime:
    def __init__(self, app):
        self.app = app
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/root",
                    "DOCKER_HOST": "unix:///var/run/docker.sock", "LC_ALL": "C",
                    "GH_PROMPT_DISABLED": "1"}

    def run(self, args, **kwargs):
        return command(args, env=self.env, **kwargs)

    def candidate(self, approved):
        sha, digest = approved["sha"], approved["digest"]
        ci = successful_ci(sha)
        tag = f"{IMAGE}:sha-{sha}"
        manifest = self.run(["docker", "buildx", "imagetools", "inspect",
                             "--format", "{{.Manifest}}", tag], capture=True)
        if re.findall(r"(?m)^Digest:\s*(sha256:[0-9a-f]{64})\s*$", manifest) != [digest]:
            raise RuntimeError("Approved digest does not match the registry SHA tag")
        ref = f"{IMAGE}@{digest}"
        self.run(["gh", "attestation", "verify", f"oci://{ref}", "--repo", REPO,
                  "--signer-workflow", f"{REPO}/{WORKFLOW}", "--source-ref", "refs/heads/dev",
                  "--source-digest", sha, "--deny-self-hosted-runners"])
        self.run(["docker", "pull", ref], timeout=300)
        revision = self.run(["docker", "image", "inspect", "--format",
                             '{{index .Config.Labels "org.opencontainers.image.revision"}}', ref],
                            capture=True)
        if revision != sha:
            raise RuntimeError("Image revision label does not match the approved SHA")
        return {**approved, "ref": ref, "run": ci, "image_id": self.image_id(ref)}

    def image_id(self, ref):
        image = self.run(["docker", "image", "inspect", "--format", "{{.Id}}", ref], capture=True)
        if not DIGEST.fullmatch(image):
            raise RuntimeError("Unexpected Docker image ID")
        return image

    def live(self):
        found = self.run(["docker", "inspect", "--type", "container", "--format",
                          '{{index .Config.Labels "com.docker.compose.project"}} {{.Image}} '
                          '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}} '
                          '{{.RestartCount}}', "rag-api"], capture=True).split()
        if (len(found) not in (4, 5) or found[0] != "app" or found[2] != "true"
                or not DIGEST.fullmatch(found[1]) or (len(found) == 5 and found[3] != "healthy")):
            raise RuntimeError("RAG container is not running as the expected Compose service")
        return found[1], int(found[-1])

    def preserve(self):
        image, _ = self.live()
        # Preserve the first deployment too, even if it was built locally.
        tag = "rag-api:cd-rollback-" + image.split(":")[1]
        self.run(["docker", "image", "tag", image, tag])
        return {"ref": tag, "image_id": image}

    def compose(self, target, args, **kwargs):
        cmd = ["docker", "compose", "--project-name", "app", "--project-directory", str(self.app),
               "--env-file", str(self.app / ".env"), "-f", str(self.app / "compose.yml")]
        return command(cmd + args, env={**self.env, "RAG_IMAGE": target["ref"]}, **kwargs)

    def preflight(self, target):
        self.compose(target, ["config", "--quiet"])
        probe = Path(__file__).with_name("verify_database.py").read_text()
        self.compose(target, ["run", "--rm", "-T", "--no-deps", "--pull", "never",
                              "--entrypoint", "python", "rag-api", "-"], stdin=probe, timeout=90)

    def activate(self, target):
        if self.image_id(target["ref"]) != target["image_id"]:
            raise RuntimeError("Local image does not match the recorded image ID")
        self.compose(target, ["up", "-d", "--no-build", "--no-deps", "--pull", "never", "rag-api"],
                     timeout=120)

    def verify(self, target):
        last_restart = None

        def check():
            nonlocal last_restart
            image, restart = self.live()
            binding = self.run(["docker", "port", "rag-api", "8000/tcp"], capture=True)
            if image != target["image_id"] or binding != "127.0.0.1:8000":
                raise RuntimeError("Unexpected running image or port binding")
            if last_restart is not None and restart != last_restart:
                last_restart = restart
                raise RuntimeError("Container restarted during verification")
            last_restart = restart
            smoke()

        wait_healthy(check)


class Deployment:
    def __init__(self, runtime, directory):
        self.runtime = runtime
        self.directory = directory
        self.path = directory / "state.json"
        self.state = read_state(self.path)

    def save(self):
        write_state(self.path, self.state)

    def audit(self, event, image):
        entry = {"time": datetime.now(timezone.utc).isoformat(), "event": event, **image}
        with (self.directory / "history.jsonl").open("a") as log:
            print(json.dumps(entry), file=log, flush=True)
            os.fsync(log.fileno())
        print(event, image.get("sha", image["image_id"]), flush=True)

    def recover(self):
        pending = self.state["pending"]
        previous = pending["previous"]
        self.runtime.activate(previous)
        self.runtime.verify(previous)
        self.state["current"] = previous
        target = pending["target"]
        if target.get("digest"):
            self.state.setdefault("failed", {})[target["digest"]] = target["sha"]
        del self.state["pending"]
        self.save()
        self.audit("rolled-back", target)

    def switch(self, target, current):
        self.state["pending"] = {"previous": current, "target": target}
        self.save()  # Persist the recovery point before touching Compose.
        self.audit("switch-started", target)
        try:
            self.runtime.activate(target)
            self.runtime.verify(target)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            self.recover()  # Failed rollback preserves pending state for the next run.
            raise
        self.state.update(current=target, previous=current)
        del self.state["pending"]
        self.save()
        self.audit("deployed", target)

    def run(self, mode, approved):
        if "pending" in self.state:
            if mode == "check":
                raise RuntimeError("Recovery pending; run the deployment service to recover")
            self.recover()
            return
        if mode == "rollback":
            current, previous = self.state.get("current"), self.state.get("previous")
            if not current or not previous:
                raise RuntimeError("No recorded previous deployment")
            preserved = self.runtime.preserve()
            if current["image_id"] != preserved["image_id"]:
                raise RuntimeError("Running image differs from deploy state; review manual drift")
            if current.get("digest"):
                self.state.setdefault("failed", {})[current["digest"]] = current["sha"]
            self.switch(previous, {**current, **preserved})
            return
        if not approved:
            print("No locally approved release")
            return
        if self.state.get("current", {}).get("digest") == approved["digest"]:
            print("Approved digest already deployed")
            return
        if approved["digest"] in self.state.get("failed", {}):
            print("Approved digest is on the failure list")
            return
        target = self.runtime.candidate(approved)
        try:
            self.runtime.preflight(target)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            if mode != "check":
                self.state.setdefault("failed", {})[target["digest"]] = target["sha"]
                self.save()
            self.audit("preflight-blocked", target)
            raise
        if mode == "check":
            self.audit("candidate-verified", target)
            return
        preserved = self.runtime.preserve()
        current = self.state.get("current", {})
        if current and current["image_id"] != preserved["image_id"]:
            raise RuntimeError("Running image differs from deploy state; review manual drift")
        fallback = {**current, **preserved}
        self.runtime.verify(fallback)
        self.switch(target, fallback)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_const", dest="mode", const="check")
    modes.add_argument("--rollback", action="store_const", dest="mode", const="rollback")
    parser.set_defaults(mode="deploy")
    options = parser.parse_args()
    if os.geteuid() != 0 or socket.gethostname().split(".")[0] != "app01":
        raise RuntimeError("Run the installed deployment helper as root on APP01")
    os.umask(0o077)
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    with locked(STATE_DIR / "deploy.lock") as acquired:
        if not acquired:
            print("Deployment lock held; skipping")
            return
        deployment = Deployment(Runtime(APP), STATE_DIR)
        # Recover even if approval was removed or became invalid after an interruption.
        approved = None if options.mode == "rollback" or "pending" in deployment.state else approval(APPROVAL)
        deployment.run(options.mode, approved)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        # Network and JSON exceptions can carry response details; log only their type.
        print(str(error) if type(error) is RuntimeError else type(error).__name__, file=sys.stderr)
        sys.exit(1)
