"""Offline checks for the E1.43b dual-build baseline and configuration gates."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location(
    "e143b_e3c3a_build_tests", ROOT / "scripts/build-expression-e1-43b-e3c3a.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
audit_spec = importlib.util.spec_from_file_location(
    "e143b_e3c3a_audit_tests", ROOT / "scripts/audit-expression-e1-43b-e3c3a-build.py")
audit = importlib.util.module_from_spec(audit_spec)
audit_spec.loader.exec_module(audit)


class BuildConfigurationTests(unittest.TestCase):
    def test_sdkconfig_toggles_only_continuous_mapping_flag(self):
        raw = ("CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE=y\n"
               "# CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT is not set\n"
               "CONFIG_BOARD_TYPE_ESP_VOCAT=y\n")
        off = build.prepare_sdkconfig(raw, False)
        on = build.prepare_sdkconfig(raw, True)
        self.assertEqual(off.count("CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT"), 1)
        self.assertEqual(on.count("CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT"), 1)
        self.assertIn("# CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT is not set\n", off)
        self.assertIn("CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT=y\n", on)
        self.assertEqual(off.replace("# CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT is not set\n", ""),
                         on.replace("CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT=y\n", ""))

    def test_effective_config_accepts_only_probe_toggle(self):
        reference = {build.FLAG: False, **{name: True for name in build.QUERY_FLAGS},
                     **{name: False for name in build.OTHER_PROBE_FLAGS}, "BOARD_TYPE_ESP_VOCAT": True}
        self.assertEqual(build.validate_config(reference, reference, False)["changed_keys"], [])
        enabled = dict(reference, **{build.FLAG: True})
        self.assertEqual(build.validate_config(reference, enabled, True)["changed_keys"], [build.FLAG])
        enabled["DESKBOT_STUDIO_STATE_QUERY_ENABLE"] = False
        with self.assertRaisesRegex(ValueError, "query features"):
            build.validate_config(reference, enabled, True)

    def test_effective_config_rejects_unrelated_delta(self):
        reference = {build.FLAG: False, **{name: True for name in build.QUERY_FLAGS},
                     **{name: False for name in build.OTHER_PROBE_FLAGS}}
        candidate = dict(reference, WIFI_SSID="unexpected")
        with self.assertRaisesRegex(ValueError, "configuration delta"):
            build.validate_config(reference, candidate, False)


class FrozenInputTests(unittest.TestCase):
    def test_verified_local_dependency_lock_is_normalized_for_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            component = (project / "components/espressif__touch_sensor_fsm").resolve()
            component.mkdir(parents=True)
            lock = ("dependencies:\n  espressif/touch_sensor_fsm:\n    source:\n"
                    f"      path: {component}\n      type: local\n")
            expected_lock = lock.replace(str(component), "components/espressif__touch_sensor_fsm")
            hashes = {"dependencies.lock": build.sha256(expected_lock.encode("utf-8"))}
            for index in range(9):
                name = f"input-{index:03d}.txt"
                raw = b"frozen\n"
                (project / name).write_bytes(raw)
                hashes[name] = build.sha256(raw)
            (project / "dependencies.lock").write_bytes(lock.encode("utf-8"))
            build.verify_frozen_sources(project, hashes)

    def test_dependency_lock_is_rebound_only_to_the_isolated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            copied = root / "copy"
            source_component = source / "components/espressif__touch_sensor_fsm"
            target_component = copied / "components/espressif__touch_sensor_fsm"
            source_component.mkdir(parents=True)
            target_component.mkdir(parents=True)
            source_lock = source / "dependencies.lock"
            copy_lock = copied / "dependencies.lock"
            lock = ("dependencies:\n  espressif/touch_sensor_fsm:\n    source:\n"
                    f"      path: {source_component.resolve()}\n      type: local\n")
            source_lock.write_bytes(lock.encode("utf-8"))
            copy_lock.write_bytes(lock.encode("utf-8"))
            build.rebind_dependency_lock(copied, source)
            self.assertIn(str(target_component.resolve()), copy_lock.read_text(encoding="utf-8"))
            self.assertIn(str(source_component.resolve()), source_lock.read_text(encoding="utf-8"))

    def test_source_modification_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            for index in range(9):
                (project / f"input-{index:03d}.txt").write_bytes(b"same")
            changed = project / "main.cc"
            changed.write_bytes(b"before")
            hashes = {path.name: build.sha256(path.read_bytes()) for path in project.iterdir()}
            changed.write_bytes(b"after")
            with self.assertRaisesRegex(ValueError, "source digest differs"):
                build.verify_frozen_sources(project, hashes)

    def test_project_inventory_rejects_unlisted_files(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "main/source.txt"
            source.parent.mkdir()
            source.write_bytes(b"frozen")
            source_manifest = {"main/source.txt": build.sha256(b"frozen")}
            with (mock.patch.object(build, "AUXILIARY_INPUT_FILES", ()),
                  mock.patch.object(build, "AUXILIARY_INPUT_ROOTS", ()),
                  mock.patch.object(build, "AUXILIARY_INPUTS_SHA256", build.sha256(b"{}"))):
                inventory = build.verify_project_file_inventory(
                    project, source_manifest, {"dependencies": {}})
                self.assertEqual(inventory["files"], 1)
                (project / "main/unlisted.txt").write_bytes(b"not in manifest")
                with self.assertRaisesRegex(ValueError, "inventory differs"):
                    build.verify_project_file_inventory(
                        project, source_manifest, {"dependencies": {}})

    def test_output_path_rejects_reparse_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = root / "tmp"
            boundary.mkdir()
            redirected = boundary / "redirected"
            actual = build._is_reparse_point

            def is_reparse(path):
                return Path(path) == redirected or actual(path)

            with mock.patch.object(build, "_is_reparse_point", side_effect=is_reparse):
                with self.assertRaisesRegex(ValueError, "reparse point"):
                    build.ensure_safe_path(redirected / "run", boundary)

    def test_probe_is_taken_from_frozen_sources_without_source_addition(self):
        facts = build.verify_probe_source(build.FROZEN)
        self.assertEqual(facts["header"], build.PROBE_HEADER)
        self.assertEqual(facts["stack_bytes"], 8192)
        self.assertFalse(facts["source_added"])


class RetainedBaselineTests(unittest.TestCase):
    def test_e3c3a_query_on_baseline_and_probe_sources_are_pinned(self):
        report, row, metadata = build.load_reference()
        self.assertEqual(report["edition"], "E3c-3a")
        self.assertEqual(row["artifacts"]["xiaozhi.bin"]["sha256"],
                         "e34d29e96bfb68eadda974b1f0a48350551a9b4d669cbf52e58db54b205d6dc3")
        self.assertEqual(metadata["probe"]["stack_bytes"], 8192)
        self.assertEqual(len(report["source_sha256"]), 1072)
        self.assertEqual(metadata["input_inventory"]["auxiliary_files"], 62)


class BinaryAuditTests(unittest.TestCase):
    def test_app_embedded_elf_digest_is_bound_to_elf_file(self):
        elf = b"built-elf"
        app = bytearray(208)
        app[176:208] = bytes.fromhex(build.sha256(elf))
        self.assertEqual(build.validate_app_elf(bytes(app), elf), build.sha256(elf))
        with self.assertRaisesRegex(ValueError, "embedded ELF digest"):
            build.validate_app_elf(bytes(app), b"different-elf")

    def test_protected_artifact_comparison_covers_every_fixed_output(self):
        baseline = {name: {"bytes": 1, "sha256": str(index)}
                    for index, name in enumerate(build.PROTECTED)}
        self.assertTrue(all(build.protected_artifact_results(baseline, baseline).values()))
        candidate = dict(baseline, **{"bootloader/bootloader.bin": {"bytes": 1, "sha256": "changed"}})
        self.assertFalse(build.protected_artifact_results(candidate, baseline)["bootloader/bootloader.bin"])

    def test_output_path_rejects_escape_from_repository_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            boundary = root / "tmp"
            boundary.mkdir()
            self.assertEqual(build.ensure_safe_path(boundary / "run", boundary), boundary / "run")
            with self.assertRaisesRegex(ValueError, "outside|traversal"):
                build.ensure_safe_path(boundary / ".." / "external", boundary)

    def test_audit_requires_each_build_configuration_and_action_once(self):
        steps = [{"configuration": config, "action": action, "returncode": 0,
                  "log": (build.WORK_ROOT / "test-run" / config / (action + ".log")).relative_to(ROOT).as_posix(),
                  "sha256": "a" * 64}
                 for config, action in (("off", "reconfigure"), ("off", "build"),
                                        ("on", "reconfigure"), ("on", "build"))]
        self.assertEqual(len(audit.validate_steps(steps, "test-run")), 4)
        with self.assertRaisesRegex(ValueError, "all four"):
            audit.validate_steps(steps[:-1], "test-run")
        with self.assertRaisesRegex(ValueError, "sequence"):
            audit.validate_steps([steps[0], steps[0], *steps[2:]], "test-run")

    def test_audit_rejects_boolean_return_code(self):
        steps = [{"configuration": config, "action": action, "returncode": 0,
                  "log": (build.WORK_ROOT / "test-run" / config / (action + ".log")).relative_to(ROOT).as_posix(),
                  "sha256": "a" * 64}
                 for config, action in (("off", "reconfigure"), ("off", "build"),
                                        ("on", "reconfigure"), ("on", "build"))]
        steps[0]["returncode"] = False
        with self.assertRaisesRegex(ValueError, "sequence"):
            audit.validate_steps(steps, "test-run")

    def test_audit_binds_step_logs_to_run_label(self):
        steps = [{"configuration": config, "action": action, "returncode": 0,
                  "log": (build.WORK_ROOT / "old-run" / config / (action + ".log")).relative_to(ROOT).as_posix(),
                  "sha256": "a" * 64}
                 for config, action in (("off", "reconfigure"), ("off", "build"),
                                        ("on", "reconfigure"), ("on", "build"))]
        with self.assertRaisesRegex(ValueError, "sequence"):
            audit.validate_steps(steps, "new-run")

    def test_audit_binds_report_and_round_paths_to_run_label(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "artifacts"
            archive.mkdir()
            report = archive / "test-run-build-report.json"
            report.write_text("{}", encoding="utf-8")
            with mock.patch.object(audit.BUILD, "ARTIFACT_ROOT", archive):
                audit.validate_report_binding(report.resolve(), "test-run")
                with self.assertRaisesRegex(ValueError, "report path"):
                    audit.validate_report_binding(report.resolve(), "other-run")

        valid = {
            "build_directory": (build.WORK_ROOT / "test-run/off/build").relative_to(ROOT).as_posix(),
            "evidence_directory": (build.WORK_ROOT / "test-run/off/evidence").relative_to(ROOT).as_posix(),
        }
        self.assertEqual(audit.validate_round_paths(valid, "test-run", "off"),
                         (build.WORK_ROOT / "test-run/off/build",
                          build.WORK_ROOT / "test-run/off/evidence"))
        valid["build_directory"] = (build.WORK_ROOT / "other-run/off/build").relative_to(ROOT).as_posix()
        with self.assertRaisesRegex(ValueError, "round path"):
            audit.validate_round_paths(valid, "test-run", "off")

    def test_audit_rejects_build_log_without_success_marker(self):
        self.assertEqual(build.validate_step_log(
            b"-- Build files have been written to: build\n", "reconfigure"),
            build.sha256(b"-- Build files have been written to: build\n"))
        self.assertEqual(build.validate_step_log(b"Project build complete.\n", "build"),
                         build.sha256(b"Project build complete.\n"))
        for raw, action in ((b"", "build"), (b"build failed\n", "build"),
                            (b"configured\n", "reconfigure")):
            with self.subTest(action=action, raw=raw):
                with self.assertRaisesRegex(ValueError, "empty|success marker"):
                    build.validate_step_log(raw, action)


if __name__ == "__main__":
    unittest.main()
