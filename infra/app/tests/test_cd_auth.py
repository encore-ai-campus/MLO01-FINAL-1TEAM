import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_pull_deploy import cd


class AuthenticationTest(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Path(self.temp.name)
        self.runtime = cd.Runtime(self.app)
        self.token = self.app / "cd" / "gh-token"
        self.token.parent.mkdir()
        self.token.write_text("test-token\n")

    @patch.object(cd, "command")
    def test_gh_receives_token(self, command):
        self.runtime.run(["gh", "attestation", "verify"])
        env = command.call_args.kwargs["env"]
        self.assertEqual(env["GH_TOKEN"], "test-token")
        self.assertNotIn("GH_TOKEN", self.runtime.env)

    @patch.object(cd, "command")
    def test_docker_does_not_receive_token(self, command):
        self.runtime.run(["docker", "pull"])
        env = command.call_args.kwargs["env"]
        self.assertNotIn("GH_TOKEN", env)

    @patch.object(cd, "command")
    def test_missing_token_blocks_gh(self, command):
        self.token.unlink()
        with self.assertRaises(FileNotFoundError):
            self.runtime.run(["gh", "attestation", "verify"])
        command.assert_not_called()
