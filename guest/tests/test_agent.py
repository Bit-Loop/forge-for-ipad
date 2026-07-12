from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "guest/python"))

from forge_guest import agent  # noqa: E402


class AgentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = {"version": 1, "token": "x" * 32, "id": "request-1"}

    def test_ping_is_authenticated_and_correlated(self) -> None:
        result = agent.dispatch({**self.request, "method": "ping"}, "x" * 32)
        self.assertEqual(result["id"], "request-1")
        self.assertEqual(result["result"], {"pong": True})

    def test_wrong_token_is_rejected(self) -> None:
        with self.assertRaisesRegex(agent.RequestError, "authentication failed"):
            agent.dispatch({**self.request, "token": "y" * 32, "method": "ping"}, "x" * 32)

    def test_unknown_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(agent.RequestError, "protocol version"):
            agent.dispatch({**self.request, "version": 2, "method": "ping"}, "x" * 32)

    def test_capabilities_do_not_offer_command_execution(self) -> None:
        methods = agent.capabilities()["methods"]
        self.assertNotIn("exec", methods)
        self.assertEqual(agent.capabilities()["execution_transport"], "ssh")

    @patch("forge_guest.agent.subprocess.run")
    def test_checkpoint_flushes_filesystems(self, run) -> None:
        result = agent.dispatch({**self.request, "method": "checkpoint"}, "x" * 32)
        run.assert_called_once_with(["sync"], check=True)
        self.assertTrue(result["result"]["flushed"])


if __name__ == "__main__":
    unittest.main()
