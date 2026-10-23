from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from protocol.deskbot_role_profile import resolve_confirmed_role, validate_role_profile


ROOT = Path(__file__).resolve().parents[1]


class RoleProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = json.loads((ROOT / "protocol/deskbot-role-profile-v1.example.json").read_text(encoding="utf-8"))

    def test_example_profile_is_valid_and_preserves_magnetic_namespace(self) -> None:
        value = validate_role_profile(self.profile)
        self.assertEqual(value["magnet_profile_id"], "offline:demo-layout-a:profile-0001")
        self.assertNotEqual(value["shell_id"], value["role_id"])

    def test_resolution_requires_known_confirmed_unique_exact_match(self) -> None:
        observation = {"status":"known", "profile_id":self.profile["magnet_profile_id"]}
        self.assertEqual(resolve_confirmed_role(observation, [self.profile])["role_id"], self.profile["role_id"])
        self.assertIsNone(resolve_confirmed_role({"status":"unknown", "profile_id":observation["profile_id"]}, [self.profile]))
        self.assertIsNone(resolve_confirmed_role(observation, [self.profile, self.profile]))
        draft = copy.deepcopy(self.profile)
        draft["binding_state"] = "draft"
        self.assertIsNone(resolve_confirmed_role(observation, [draft]))

    def test_rejects_secret_fields_and_nonordinary_actions(self) -> None:
        secret = copy.deepcopy(self.profile)
        secret["api_key"] = "do-not-accept"
        with self.assertRaisesRegex(ValueError, "unsupported role profile fields"):
            validate_role_profile(secret)
        unsafe = copy.deepcopy(self.profile)
        unsafe["action_map"]["bad"] = "calibrate"
        with self.assertRaisesRegex(ValueError, "approved body action"):
            validate_role_profile(unsafe)

    def test_limits_and_namespace_are_preserved(self) -> None:
        self.profile["magnet_profile_id"] = "offline:72b0eac6ea3ccb64:profile-0001"
        validate_role_profile(self.profile)
        self.assertIsNone(resolve_confirmed_role({"status":"known","profile_id":"profile-0001"},[self.profile]))
        for key,value in (("display_name","  "),("profile_revision",True),("profile_revision",2**53)):
            invalid=copy.deepcopy(self.profile)
            invalid[key]=value
            with self.assertRaises(ValueError):
                validate_role_profile(invalid)

    def test_binding_version_is_optional_for_legacy_v1_and_validated_when_present(self) -> None:
        legacy = copy.deepcopy(self.profile)
        self.assertNotIn("binding_version", legacy)
        validate_role_profile(legacy)
        self.profile["binding_version"] = "deskbot.expression_bindings/v1"
        validate_role_profile(self.profile)
        for version in (None, "not-versioned", "deskbot.expression_bindings/v0", "x" * 97):
            self.profile["binding_version"] = version
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, "binding_version"):
                validate_role_profile(self.profile)


if __name__ == "__main__":
    unittest.main()
