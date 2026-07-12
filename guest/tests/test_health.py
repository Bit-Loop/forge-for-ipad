from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "guest/python"))

from forge_guest import health  # noqa: E402


class HealthTests(unittest.TestCase):
    @patch("forge_guest.health.systemd_active", return_value=True)
    @patch("forge_guest.health.command_available", return_value=True)
    def test_health_payload_is_versioned(self, _available, _active) -> None:
        result = health.collect()
        self.assertEqual(result["schema"], 1)
        self.assertTrue(result["healthy"])
        self.assertIn("compiler.c", result["checks"])
        self.assertIn("agent", result["checks"])


if __name__ == "__main__":
    unittest.main()
