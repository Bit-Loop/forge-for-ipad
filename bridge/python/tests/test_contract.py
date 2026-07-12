from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ImportError:  # The runtime client deliberately has no dependencies.
    Draft202012Validator = None  # type: ignore[assignment,misc]

BRIDGE = Path(__file__).resolve().parents[2]
SCHEMA = BRIDGE / "schema" / "forge-accelerator-v1.schema.json"
OPENAPI = BRIDGE / "openapi" / "forge-accelerator-v1.openapi.json"


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA.read_text())
        cls.openapi = json.loads(OPENAPI.read_text())

    def test_documents_are_versioned_and_guest_only(self) -> None:
        self.assertEqual(self.openapi["openapi"], "3.1.0")
        self.assertEqual(self.openapi["info"]["version"], "1.0.0")
        self.assertEqual(
            self.openapi["servers"][0]["url"],
            "http://10.0.2.2:4777/accelerator/v1",
        )
        self.assertEqual(self.schema["$defs"]["ProtocolVersion"]["const"], "1.0")
        self.assertEqual(self.openapi["security"], [{"perBootBearer": []}])

    def test_all_external_schema_references_resolve(self) -> None:
        definitions = self.schema["$defs"]

        def walk(value: object) -> None:
            if isinstance(value, dict):
                reference = value.get("$ref")
                if isinstance(reference, str) and reference.startswith("../schema/"):
                    name = reference.rsplit("/", 1)[-1]
                    self.assertIn(name, definitions, reference)
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(self.openapi)

    def test_json_schema_is_valid_draft_2020_12(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("install forge-accelerator[test] for full schema validation")
        Draft202012Validator.check_schema(self.schema)

    def test_v1_scratch_contract_is_regular_files_only(self) -> None:
        properties = self.schema["$defs"]["ScratchReference"]["properties"]
        self.assertNotIn("content_encoding", properties)
        self.assertNotIn("expanded_size", properties)
        self.assertNotIn(
            "content_encodings",
            self.schema["$defs"]["Capabilities"]["properties"]["scratch"]["properties"],
        )
        self.assertEqual(
            self.schema["$defs"]["CoreMLCompileRequest"]["properties"]["format"],
            {"const": "mlmodel"},
        )
        if Draft202012Validator is None:
            self.skipTest("install forge-accelerator[test] for validation behavior")
        validator = Draft202012Validator(
            {
                "$ref": "#/$defs/ScratchReference",
                "$defs": self.schema["$defs"],
            }
        )
        errors = list(
            validator.iter_errors(
                {
                    "relative_path": "models/model.mlmodel",
                    "sha256": "0" * 64,
                    "size": 1,
                    "content_encoding": "zip",
                    "expanded_size": 2,
                }
            )
        )
        self.assertTrue(errors, "legacy archive metadata must be rejected as unknown")

    def test_every_operation_declares_auth_and_structured_errors(self) -> None:
        operations = 0
        for path in self.openapi["paths"].values():
            for method, operation in path.items():
                if method not in {"get", "post", "delete", "put", "patch"}:
                    continue
                operations += 1
                responses = operation["responses"]
                self.assertIn("401", responses)
                self.assertIn("default", responses)
        self.assertEqual(operations, 11)

    def test_c_and_cpp_headers_compile(self) -> None:
        c_source = '#include "forge_accelerator.h"\nint main(void){return 0;}\n'
        cpp_source = '#include "forge_accelerator.hpp"\nint main(){return 0;}\n'
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            c_file = directory_path / "test.c"
            cpp_file = directory_path / "test.cpp"
            c_file.write_text(c_source)
            cpp_file.write_text(cpp_source)
            cc = shutil.which("cc")
            cxx = shutil.which("c++")
            self.assertIsNotNone(cc)
            self.assertIsNotNone(cxx)
            subprocess.run(  # noqa: S603 - fixed compiler path and test-authored input
                [
                    str(cc),
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(BRIDGE / "include"),
                    "-fsyntax-only",
                    str(c_file),
                ],
                check=True,
            )
            subprocess.run(  # noqa: S603 - fixed compiler path and test-authored input
                [
                    str(cxx),
                    "-std=c++20",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(BRIDGE / "include"),
                    "-fsyntax-only",
                    str(cpp_file),
                ],
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
