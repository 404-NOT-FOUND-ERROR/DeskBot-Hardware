from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None


ROOT = Path(__file__).resolve().parents[1]


class BodyFactsProtocolSmokeTest(unittest.TestCase):
    def test_schema_and_example_are_json_and_versioned(self) -> None:
        schema = json.loads((ROOT / "protocol" / "deskbot-body-facts-v1.schema.json").read_text(encoding="utf-8"))
        example = json.loads((ROOT / "protocol" / "deskbot-body-facts-v1.example.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["$id"], "https://deskbot.local/protocol/deskbot-body-facts-v1.schema.json")
        self.assertEqual(example["schema"], "deskbot.body.v1")
        self.assertEqual(example["payload"]["kind"], "snapshot")
        self.assertNotIn("role_id", example["payload"]["shell_observation"])
        self.assertNotIn("api_key", example)

    def test_identity_names_are_not_collapsed(self) -> None:
        example = json.loads((ROOT / "protocol" / "deskbot-body-facts-v1.example.json").read_text(encoding="utf-8"))
        shell = example["payload"]["shell_observation"]
        expression = example["payload"]["expression"]
        self.assertIsInstance(shell["profile_id"], str)
        self.assertNotEqual(shell["profile_id"], expression["asset_version"])


@unittest.skipIf(Draft202012Validator is None, "jsonschema is required for full JSON Schema validation")
class BodyFactsSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads((ROOT / "protocol" / "deskbot-body-facts-v1.schema.json").read_text(encoding="utf-8"))
        self.example = json.loads((ROOT / "protocol" / "deskbot-body-facts-v1.example.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def test_example_and_documented_example_validate(self) -> None:
        self.validator.validate(self.example)
        document = (ROOT / "docs" / "client-body-facts-v1-20261018.md").read_text(encoding="utf-8")
        documented_example = json.loads(document.split("```json\n", 1)[1].split("```", 1)[0])
        self.validator.validate(documented_example)

    def test_unknown_identity_and_invalid_timestamp_are_rejected(self) -> None:
        invalid = deepcopy(self.example)
        invalid["payload"]["shell_observation"]["status"] = "unknown"
        with self.assertRaises(ValidationError):
            self.validator.validate(invalid)
        invalid = deepcopy(self.example)
        invalid["occurred_at"] = 123
        with self.assertRaises(ValidationError):
            self.validator.validate(invalid)

    def test_all_final_f1_exports_validate_when_present(self) -> None:
        runs = ROOT / "artifacts" / "body-facts-replay-20261018" / "runs"
        names = ("eval-shell-a-ab-01", "eval-shell-b-01", "eval-unknown-01", "eval-removed-02", "eval-replace-01")
        for name in names:
            path = runs / f"f1-final3-{name}" / "messages.jsonl"
            if not path.is_file():
                self.skipTest("final real F1 exports have not been generated")
            with self.subTest(segment=name):
                messages = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                self.assertEqual([message["message_type"] for message in messages], ["hello", "snapshot"])
                for message in messages:
                    self.validator.validate(message)


if __name__ == "__main__":
    unittest.main()
