from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "images/scripts/imagectl.py"
SPEC = importlib.util.spec_from_file_location("imagectl", MODULE_PATH)
assert SPEC and SPEC.loader
imagectl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(imagectl)


class ImageContractTests(unittest.TestCase):
    def test_repository_contract_is_valid(self) -> None:
        self.assertEqual(imagectl.validate(), {"sources": 8, "packs": 9, "images": 3})

    def test_every_image_has_a_complete_plan(self) -> None:
        for image_id in ("ubuntu-seed", "manjaro-arm", "archlinuxarm-lxc"):
            with self.subTest(image=image_id):
                plan = imagectl.plan(image_id)
                self.assertEqual(plan["image"], image_id)
                self.assertTrue(plan["packs"])
                self.assertEqual(plan["channel"], "stable")

    def test_seed_source_uses_sha256(self) -> None:
        source = imagectl.source_map()["ubuntu-26.04-arm64-cloudimg"]
        self.assertEqual(source["checksum_algorithm"], "sha256")
        self.assertTrue(source["release_eligible"])

    def test_manjaro_requires_mirrored_sha256_before_release(self) -> None:
        source = imagectl.source_map()["manjaro-arm-minimal-generic-23.02"]
        image = imagectl.image_map()["manjaro-arm"]
        self.assertFalse(source["release_eligible"])
        self.assertTrue(image["release_requires_mirrored_sha256"])
        with self.assertRaisesRegex(imagectl.ContractError, "not release eligible"):
            imagectl.release_check("manjaro-arm")

    def test_ubuntu_and_arch_sources_are_release_eligible(self) -> None:
        self.assertTrue(imagectl.release_check("ubuntu-seed")["release_eligible"])
        self.assertTrue(imagectl.release_check("archlinuxarm-lxc")["release_eligible"])

    def test_hash_verification_accepts_expected_bytes(self) -> None:
        with tempfile.NamedTemporaryFile() as source:
            source.write(b"forge\n")
            source.flush()
            self.assertEqual(
                imagectl.digest(Path(source.name), "sha256"),
                "b036dee0a8d15016320782000503a31f3a2898d287ff82b03afe5f3cfaefe0c1",
            )

    def test_unknown_image_is_rejected(self) -> None:
        with self.assertRaisesRegex(imagectl.ContractError, "unknown image"):
            imagectl.plan("not-an-image")


if __name__ == "__main__":
    unittest.main()
