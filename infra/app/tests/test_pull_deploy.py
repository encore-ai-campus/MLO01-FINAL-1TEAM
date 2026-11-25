"""Exercise the host deployment transaction without contacting APP01 or GHCR."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pull_deploy", ROOT / "pull-deploy.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
OLD = {"ref": "rag-api:local", "image_id": "sha256:" + "1" * 64}
APPROVED = {"sha": SHA, "digest": DIGEST}
NEW = {**APPROVED, "ref": f"{cd.IMAGE}@{DIGEST}", "run": 12,
       "image_id": "sha256:" + "2" * 64}


class Fixture:
    def __init__(self, app):
        self.app = app
        self.active = OLD
        self.switches = []
        self.schema_error = False
        self.api_error = False
        self.interrupted = False

    def candidate(self, approved):
        if approved != APPROVED:
            raise ValueError("unexpected candidate")
        return NEW

    def preflight(self, target):
        if self.schema_error:
            raise RuntimeError("database contract failed")

    def preserve(self):
        return self.active

    def activate(self, target):
        self.active = target
        self.switches.append(target)

    def verify(self, target):
        if target == NEW:
            if self.interrupted:
                raise KeyboardInterrupt
            if self.api_error:
                raise RuntimeError("API failed")
        if self.active != target:
            raise RuntimeError("wrong image")


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.env = self.path / ".env"
        self.original = b"DATABASE_URL=postgresql://example.invalid/dummy\n"
        self.env.write_bytes(self.original)
        self.runtime = Fixture(self.path)

    def tearDown(self):
        self.assertEqual(self.env.read_bytes(), self.original)

    def deploy(self, mode="deploy", approved=APPROVED):
        cd.Deployment(self.runtime, self.path).run(mode, approved)
        return cd.read_state(self.path / "state.json")

    def test_success_idempotency_and_manual_rollback(self):
        state = self.deploy()
        self.assertEqual(state["current"], NEW)
        self.assertEqual(state["previous"], OLD)
        self.deploy()
        self.assertEqual(self.runtime.switches, [NEW])
        state = self.deploy("rollback", None)
        self.assertEqual(state["current"], OLD)
        self.assertEqual(state["failed"], {DIGEST: SHA})
        self.deploy()
        self.assertEqual(self.runtime.switches, [NEW, OLD])
        history = (self.path / "history.jsonl").read_text()
        self.assertNotIn("DATABASE_URL", history)

    def test_no_approval_skips_candidate(self):
        with patch.object(self.runtime, "candidate") as candidate:
            self.deploy(approved=None)
            candidate.assert_not_called()
        self.assertFalse(self.runtime.switches)

    def test_api_failure_restores_old_image_and_blocks_retry(self):
        self.runtime.api_error = True
        with self.assertRaisesRegex(RuntimeError, "API failed"):
            self.deploy()
        self.assertEqual(self.runtime.switches, [NEW, OLD])
        state = self.deploy()
        self.assertEqual(state["failed"], {DIGEST: SHA})
        self.assertEqual(self.runtime.switches, [NEW, OLD])

    def test_interruption_restores_on_next_run_even_without_approval(self):
        self.runtime.interrupted = True
        with self.assertRaises(KeyboardInterrupt):
            self.deploy()
        self.assertEqual(self.runtime.active, NEW)
        state = self.deploy(approved=None)
        self.assertNotIn("pending", state)
        self.assertEqual(state["current"], OLD)
        self.assertEqual(self.runtime.switches, [NEW, OLD])

    def test_bad_database_schema_blocks_before_switch(self):
        self.runtime.schema_error = True
        with self.assertRaisesRegex(RuntimeError, "database contract failed"):
            self.deploy()
        self.assertEqual(self.runtime.switches, [])
        self.assertEqual(self.deploy()["failed"], {DIGEST: SHA})

    def test_concurrent_invocation_does_not_acquire_lock(self):
        with cd.locked(self.path / "deploy.lock") as first, cd.locked(self.path / "deploy.lock") as second:
            self.assertTrue(first)
            self.assertFalse(second)

    def test_host_command_uses_existing_env_file_and_image_only_override(self):
        runtime = cd.Runtime(self.path)
        with patch.object(cd, "command") as command:
            runtime.compose(NEW, ["config", "--quiet"])
        args = command.call_args.args[0]
        self.assertIn(str(self.env), args)
        self.assertEqual(command.call_args.kwargs["env"]["RAG_IMAGE"], NEW["ref"])
        self.assertNotIn("DATABASE_URL", command.call_args.kwargs["env"])


class CandidateTest(unittest.TestCase):
    def test_failed_ci_blocks_before_any_docker_or_attestation_command(self):
        runtime = cd.Runtime(Path("/opt/app"))
        run = {"id": 42, "path": cd.WORKFLOW, "head_sha": SHA, "head_branch": "dev",
               "head_repository": {"full_name": cd.REPO}, "event": "push",
               "status": "completed", "conclusion": "failure"}
        with patch.object(cd, "github_json", return_value={"workflow_runs": [run]}), patch.object(runtime, "run") as command:
            with self.assertRaisesRegex(RuntimeError, "No successful"):
                runtime.candidate(APPROVED)
            command.assert_not_called()

    def test_successful_rag_without_publication_is_not_eligible(self):
        run = {"id": 42, "path": cd.WORKFLOW, "head_sha": SHA, "head_branch": "dev",
               "head_repository": {"full_name": cd.REPO}, "event": "push",
               "status": "completed", "conclusion": "success"}
        with patch.object(cd, "github_json", side_effect=[{"workflow_runs": [run]}, {"jobs": []}]):
            with self.assertRaisesRegex(RuntimeError, "No successful"):
                cd.successful_ci(SHA)

    def test_mismatched_tag_digest_blocks_before_attestation(self):
        runtime = cd.Runtime(Path("/opt/app"))
        with patch.object(cd, "successful_ci", return_value=12), patch.object(runtime, "run", return_value="Digest: " + OLD["image_id"]) as command:
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                runtime.candidate(APPROVED)
            self.assertEqual(command.call_count, 1)

    def test_attestation_failure_blocks_pull(self):
        runtime = cd.Runtime(Path("/opt/app"))
        output = ["Digest: " + DIGEST, RuntimeError("attestation failed")]
        with patch.object(cd, "successful_ci", return_value=12), patch.object(runtime, "run", side_effect=output) as command:
            with self.assertRaisesRegex(RuntimeError, "attestation failed"):
                runtime.candidate(APPROVED)
            self.assertEqual(command.call_count, 2)
            flags = command.call_args.args[0]
            self.assertIn("refs/heads/dev", flags)
            self.assertIn(SHA, flags)
            self.assertIn(f"{cd.REPO}/{cd.WORKFLOW}", flags)
            self.assertIn("--deny-self-hosted-runners", flags)

    def test_verified_digest_is_pulled_and_revision_is_checked(self):
        runtime = cd.Runtime(Path("/opt/app"))
        output = ["Digest: " + DIGEST, None, None, SHA, NEW["image_id"]]
        with patch.object(cd, "successful_ci", return_value=12), patch.object(runtime, "run", side_effect=output) as command:
            self.assertEqual(runtime.candidate(APPROVED), NEW)
            self.assertEqual(command.call_args_list[2].args[0], ["docker", "pull", NEW["ref"]])


if __name__ == "__main__":
    unittest.main()
