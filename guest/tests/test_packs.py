from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "guest/python"))

from forge_guest import packs  # noqa: E402


class PackTests(unittest.TestCase):
    def test_dependencies_are_topologically_ordered_and_deduplicated(self) -> None:
        plan = packs.plan(ROOT / "images/packs", ["rust", "python"], "apt")
        self.assertEqual(plan["packs"][:2], ["core", "cpp"])
        self.assertEqual(plan["packs"].count("cpp"), 1)
        self.assertEqual(plan["packages"], sorted(set(plan["packages"])))

    def test_full_workstation_includes_transitive_packs(self) -> None:
        plan = packs.plan(ROOT / "images/packs", ["workstation"], "pacman")
        self.assertTrue({"core", "cpp", "rust", "python", "containers", "xfce"} <= set(plan["packs"]))
        self.assertIn("clang", plan["packages"])
        self.assertIn("python", plan["packages"])
        self.assertIn("numpy", plan["python_environment"])
        self.assertNotIn("numpy", plan["python_tools"])

    def test_unknown_pack_is_rejected(self) -> None:
        with self.assertRaisesRegex(packs.PackError, "unknown pack"):
            packs.plan(ROOT / "images/packs", ["missing"], "apt")

    def test_heavy_ml_requires_binary_wheels(self) -> None:
        plan = packs.plan(ROOT / "images/packs", ["ml"], "apt")
        self.assertIn("torch", plan["python_binary_only"])
        self.assertNotIn("torch", plan["python_environment"])

    def test_cycle_is_rejected(self) -> None:
        data = {"a": {"id": "a", "depends": ["b"]}, "b": {"id": "b", "depends": ["a"]}}
        with self.assertRaisesRegex(packs.PackError, "cycle"):
            packs.resolve(data, ["a"])

    def test_distro_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "os-release"
            release.write_text('ID="manjaro-arm"\n')
            self.assertEqual(packs.detect_distro(release), "pacman")
            release.write_text("ID=ubuntu\n")
            self.assertEqual(packs.detect_distro(release), "apt")


if __name__ == "__main__":
    unittest.main()
