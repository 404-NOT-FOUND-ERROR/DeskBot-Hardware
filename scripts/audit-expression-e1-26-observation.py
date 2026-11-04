#!/usr/bin/env python3
"""Audit the E1.26 opt-in expression-call observer without hardware access."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = "artifacts/expression-e1-26-runtime-observation-20261026/report.json"
BASELINE_COMMIT = "747b0d918538eedaf936fda0bb6c9340fc7658cc"
EXPECTED_FIRMWARE_PATHS = {
    "firmware/body/SOURCE_MANIFEST.sha256",
    "firmware/body/main/CMakeLists.txt",
    "firmware/body/main/Kconfig.projbuild",
    "firmware/body/main/boards/esp_vocat/dev_tools.cc",
    "firmware/body/main/display/emote_display.cc",
    "firmware/body/main/display/expression_runtime_observer.cc",
    "firmware/body/main/display/expression_runtime_observer.h",
}
IDF_INPUTS = (
    "firmware/body/main/display/expression_runtime_observer.cc",
    "firmware/body/main/display/expression_runtime_observer.h",
    "tests/idf-expression-observer/CMakeLists.txt",
    "tests/idf-expression-observer/main/CMakeLists.txt",
    "tests/idf-expression-observer/main/Kconfig.projbuild",
    "tests/idf-expression-observer/main/app_main.cc",
    "tests/idf-expression-observer/sdkconfig.defaults.off",
    "tests/idf-expression-observer/sdkconfig.defaults.on",
)


@dataclass(frozen=True)
class InputFile:
    relative: str
    path: Path | None = None
    commit: str | None = None

    def read_bytes(self) -> bytes:
        if self.path is not None:
            return self.path.read_bytes()
        if self.commit is None:
            raise ValueError(f"no source for {self.relative}")
        return subprocess.run(
            ["git", "show", f"{self.commit}:{self.relative}"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout

    def read_text(self) -> str:
        return self.read_bytes().decode("utf-8")

    def sha256(self) -> str:
        return hashlib.sha256(self.read_bytes()).hexdigest()


def working_file(relative: str) -> InputFile:
    path = (ROOT / relative).resolve()
    if not path.is_file():
        raise ValueError(f"missing required input: {relative}")
    return InputFile(relative, path=path)


def baseline_file(relative: str) -> InputFile:
    subprocess.run(["git", "cat-file", "-e", f"{BASELINE_COMMIT}:{relative}"],
                   cwd=ROOT, check=True, capture_output=True)
    return InputFile(relative, commit=BASELINE_COMMIT)


def normalized(text: str) -> str:
    return text.replace("\r\n", "\n")


def remove_exact(text: str, block: str, label: str) -> str:
    text = normalized(text)
    block = normalized(block)
    count = text.count(block)
    if count != 1:
        raise ValueError(f"expected one {label} block, found {count}")
    return text.replace(block, "", 1)


def audit_integration_files() -> dict[str, Any]:
    # Each block is explicit and small. Removing exactly these blocks must
    # reproduce the E1.25 baseline byte-for-byte after line-ending normalization.
    blocks = {
        "firmware/body/main/CMakeLists.txt": [
            "\n# E1.26 is an opt-in diagnostic.  Keep its code out of the normal firmware\n"
            "# build so the default image has no observation hook or additional MCP tools.\n"
            "if(CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE)\n"
            "    list(APPEND SOURCES \"display/expression_runtime_observer.cc\")\n"
            "endif()\n",
        ],
        "firmware/body/main/Kconfig.projbuild": [
            "config DESKBOT_EXPRESSION_OBSERVER_ENABLE\n"
            "    bool \"Enable passive expression-call observation (E1.26)\"\n"
            "    depends on BOARD_TYPE_ESP_VOCAT\n"
            "    default n\n"
            "    help\n"
            "        Keep a bounded, volatile record of semantic calls entering\n"
            "        EmoteDisplay::SetEmotion. This does not report resource loading or LCD\n"
            "        rendering. When disabled, the observer source, call hook and MCP tools\n"
            "        are omitted from the firmware build.\n\n",
        ],
        "firmware/body/main/boards/esp_vocat/dev_tools.cc": [
            "#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE\n"
            "#include \"expression_runtime_observer.h\"\n"
            "#endif\n",
            "#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE\n"
            "    // Explicitly enabled E1.26 tools. Both operations are volatile and never\n"
            "    // command the display, player, base, network or persistent storage.\n"
            "    mcp_server.AddTool(\"self.expression.observation\",\n"
            "        is_zh ? \"读取最近的表情语义调用记录（只读、易失；仅表示调用到达，不代表资源已加载或屏幕已呈现）。\"\n"
            "              : \"Read recent volatile expression semantic calls (read-only; call arrival does not prove loading or rendering).\",\n"
            "        PropertyList(), [](const PropertyList&) -> ReturnValue {\n"
            "            const uint32_t now_ms = static_cast<uint32_t>(\n"
            "                esp_timer_get_time() / 1000);\n"
            "            return ExpressionRuntimeObserver::GetInstance().Describe(now_ms);\n"
            "        });\n\n"
            "    mcp_server.AddTool(\"self.expression.observation.clear\",\n"
            "        is_zh ? \"清除本次启动期间的易失表情调用记录和丢弃计数（不会重置序号）。\"\n"
            "              : \"Clear volatile expression-call records and drop counters for this boot; the sequence is preserved.\",\n"
            "        PropertyList(), [](const PropertyList&) -> ReturnValue {\n"
            "            return ExpressionRuntimeObserver::GetInstance().Clear();\n"
            "        });\n"
            "#endif\n\n",
        ],
        "firmware/body/main/display/emote_display.cc": [
            "#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE\n"
            "#include \"expression_runtime_observer.h\"\n"
            "#endif\n",
            "#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE\n"
            "    ExpressionRuntimeObserver::GetInstance().Observe(\n"
            "        emotion, static_cast<uint32_t>(esp_timer_get_time() / 1000));\n"
            "#endif\n",
        ],
    }
    checks: dict[str, Any] = {}
    for relative, allowed_blocks in blocks.items():
        current = working_file(relative).read_text()
        baseline = baseline_file(relative).read_text()
        for index, block in enumerate(allowed_blocks):
            current = remove_exact(current, block, f"{relative} block {index + 1}")
        current_normalized = normalized(current)
        baseline_normalized = normalized(baseline)
        checks[relative] = {
            "matches_baseline_after_guard_removal": current_normalized == baseline_normalized,
            "current_sha256": working_file(relative).sha256(),
            "baseline_sha256": baseline_file(relative).sha256(),
        }
        if current_normalized != baseline_normalized:
            raise ValueError(f"official source drift outside E1.26 guard: {relative}")
    return checks


def status_paths() -> set[str]:
    tracked = subprocess.run(
        ["git", "diff", "--name-only", BASELINE_COMMIT, "--", "firmware/body"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "--", "firmware/body"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    return {line.replace("\\", "/") for line in (tracked + "\n" + untracked).splitlines()
            if line.strip()}


def audit_manifest() -> dict[str, Any]:
    manifest = ROOT / "firmware/body/SOURCE_MANIFEST.sha256"
    if not manifest.is_file():
        raise ValueError("firmware/body/SOURCE_MANIFEST.sha256 is missing")
    listed: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError("malformed source-manifest row") from exc
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"invalid SHA-256 in source manifest: {relative}")
        if relative in listed:
            raise ValueError(f"duplicate source-manifest path: {relative}")
        listed[relative] = digest
    check = subprocess.run(
        [os.environ.get("PYTHON", "python"), str(ROOT / "scripts/update-body-source-manifest.py"), "--check"],
        cwd=ROOT, check=False, capture_output=True, text=True, encoding="utf-8",
    )
    if check.returncode != 0:
        raise ValueError("body source manifest is stale: " + check.stdout.strip())
    return {"entries": len(listed), "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "check": "scripts/update-body-source-manifest.py --check passed"}


def audit_contract() -> dict[str, Any]:
    observer_h = working_file("firmware/body/main/display/expression_runtime_observer.h")
    observer_cc = working_file("firmware/body/main/display/expression_runtime_observer.cc")
    schema = working_file("protocol/expression-runtime-observation-e1-26.schema.json")
    model = working_file("protocol/expression_runtime_observation_e1_26.py")
    cpp_test = working_file("tests/cpp/expression_runtime_observer_test.cc")
    h = observer_h.read_text()
    cc = observer_cc.read_text()
    if "default n" not in working_file("firmware/body/main/Kconfig.projbuild").read_text():
        raise ValueError("observer option is not default-off")
    for marker in ("kCapacity = 24", "kCoalesceWindowMs = 250", "kMaxSemanticBytes = 24",
                   "portMUX_TRY_LOCK", "volatile uint32_t", "__atomic_load_n",
                   "__atomic_store_n"):
        if marker not in h + cc:
            raise ValueError(f"observer contract marker missing: {marker}")
    forbidden = ("malloc(", "calloc(", "realloc(", "new ", "std::vector", "esp_log")
    if any(marker in cc for marker in forbidden):
        raise ValueError("observer contains forbidden allocation/logging marker")
    schema_obj = json.loads(schema.read_text())
    Draft202012Validator.check_schema(schema_obj)
    if schema_obj.get("$id") != "https://deskbot.local/protocol/expression-runtime-observation-e1-26.schema.json":
        raise ValueError("unexpected E1.26 schema id")
    if schema_obj.get("properties", {}).get("schema", {}).get("const") != model.read_text().split('SCHEMA_ID = "', 1)[1].split('"', 1)[0]:
        raise ValueError("JSON snapshot schema value differs from the host protocol model")
    if schema_obj.get("properties", {}).get("records", {}).get("maxItems") != 24:
        raise ValueError("schema record capacity drift")
    if "self.expression.observation" not in working_file("firmware/body/main/boards/esp_vocat/dev_tools.cc").read_text():
        raise ValueError("read-only observation tool missing")
    return {
        "observer_cc_sha256": observer_cc.sha256(),
        "observer_h_sha256": observer_h.sha256(),
        "schema_sha256": schema.sha256(),
        "model_sha256": model.sha256(),
        "cpp_test_sha256": cpp_test.sha256(),
        "capacity": 24,
        "coalesce_window_ms": 250,
        "default_enabled": False,
        "volatile_ram_only": True,
        "read_only_tools": ["self.expression.observation", "self.expression.observation.clear"],
    }


def write_json(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                     dir=path.parent, prefix=path.name + ".", suffix=".tmp",
                                     delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def load_report(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"missing {label} report: {path.relative_to(ROOT)}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label} report JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} report must be an object")
    return value


def audit_cpp_report(path: Path) -> dict[str, Any]:
    report = load_report(path, "host C++")
    expected_hashes = {
        "observer_cc": working_file("firmware/body/main/display/expression_runtime_observer.cc").sha256(),
        "observer_h": working_file("firmware/body/main/display/expression_runtime_observer.h").sha256(),
        "test_cc": working_file("tests/cpp/expression_runtime_observer_test.cc").sha256(),
    }
    if report.get("result") != "host_cpp_observer_tests_passed" or report.get("audit_pass") is not True:
        raise ValueError("host C++ report does not record a passing test run")
    if report.get("source_sha256") != expected_hashes:
        raise ValueError("host C++ report is stale for the current observer/test sources")
    if report.get("json_description_schema_valid") is not True:
        raise ValueError("host C++ JSON output did not pass schema validation")
    configurations = report.get("configurations")
    if not isinstance(configurations, list) or {item.get("enabled") for item in configurations} != {False, True}:
        raise ValueError("host C++ report must cover both observer configurations")
    if any(item.get("compile_pass") is not True or item.get("run_pass") is not True
           for item in configurations):
        raise ValueError("a host C++ observer configuration did not compile and run")
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "result": report["result"], "source_hashes_match": True}


def audit_minimal_idf_report(path: Path) -> dict[str, Any]:
    report = load_report(path, "minimal ESP-IDF")
    expected_hashes = {name: working_file(name).sha256() for name in IDF_INPUTS}
    if report.get("result") != "minimal_idf_preflight_passed" or report.get("audit_pass") is not True:
        raise ValueError("minimal ESP-IDF report does not record a passing preflight")
    if report.get("source_sha256") != expected_hashes:
        raise ValueError("minimal ESP-IDF report is stale for the current source inputs")
    if report.get("target") != "esp32s3" or report.get("deployable_deskbot_image") is not False:
        raise ValueError("minimal ESP-IDF report has an unexpected target or deployment claim")
    safety = report.get("safety", {})
    if any(safety.get(key) is not False for key in (
            "hardware_accessed", "serial_accessed", "device_written", "nvs_changed", "flash_executed")):
        raise ValueError("minimal ESP-IDF report contains an unexpected hardware/flash claim")
    configurations = report.get("configurations")
    if not isinstance(configurations, list) or {item.get("enabled") for item in configurations} != {False, True}:
        raise ValueError("minimal ESP-IDF report must cover both observer configurations")
    verified = []
    for item in configurations:
        enabled = item["enabled"]
        build_relative = Path(item["build_directory"])
        if build_relative.is_absolute() or not build_relative.parts or build_relative.parts[0] != "tmp":
            raise ValueError("minimal IDF build output must remain under tmp/")
        build = (ROOT / build_relative).resolve()
        try:
            build.relative_to((ROOT / "tmp").resolve())
        except ValueError as exc:
            raise ValueError("minimal IDF build directory escapes tmp/") from exc
        if (item.get("build_pass") is not True or item.get("config_matches_requested") is not True
                or item.get("source_membership_matches_requested") is not True
                or item.get("config_value") is not enabled
                or item.get("observer_source_member") is not enabled
                or any(step.get("returncode") != 0 for step in item.get("steps", []))):
            raise ValueError("minimal IDF configuration did not match the requested observer state")
        sdkconfig = build / "sdkconfig"
        if not sdkconfig.is_file() or hashlib.sha256(sdkconfig.read_bytes()).hexdigest() != item.get("sdkconfig_sha256"):
            raise ValueError("minimal IDF sdkconfig is missing, stale, or has the wrong hash")
        artifacts = item.get("expected_artifacts", {})
        for relative in ("bootloader/bootloader.bin", "partition_table/partition-table.bin",
                         "e1_26_expression_observer_preflight.bin"):
            if artifacts.get(relative) is not True or not (build / relative).is_file():
                raise ValueError("minimal IDF preflight artifact missing: " + relative)
        binary = build / "e1_26_expression_observer_preflight.bin"
        recorded_binary = item.get("binary", {})
        if (binary.stat().st_size != recorded_binary.get("bytes")
                or hashlib.sha256(binary.read_bytes()).hexdigest() != recorded_binary.get("sha256")):
            raise ValueError("minimal IDF binary hash/size differs from its report")
        verified.append({"enabled": enabled, "build_directory": build_relative.as_posix(),
                         "binary_bytes": binary.stat().st_size,
                         "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()})
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "result": report["result"], "configurations": verified}


def audit_full_body_report(path: Path) -> dict[str, Any]:
    report = load_report(path, "locked full-body build")
    if (report.get("schema") != "deskbot.expression.e1_26_full_body_build/v1"
            or report.get("edition") != "E1.26"
            or report.get("result") != "locked_full_body_builds_passed"
            or report.get("audit_pass") is not True
            or report.get("full_firmware_build_passed") is not True):
        raise ValueError("locked full-body report does not record two passing firmware builds")
    if report.get("deployment_eligible") is not False or report.get("flash_authorized") is not False:
        raise ValueError("offline build report must not authorize deployment or flashing")
    safety = report.get("safety", {})
    if any(safety.get(key) is not False for key in (
            "hardware_accessed", "serial_accessed", "device_written", "nvs_changed", "flash_executed")):
        raise ValueError("full-body report contains an unexpected hardware/flash claim")
    manifest_path = ROOT / "firmware/body/SOURCE_MANIFEST.sha256"
    if report.get("source_manifest_sha256") != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
        raise ValueError("full-body report is stale for the current source manifest")
    expected_sources = {}
    for row in manifest_path.read_text(encoding="utf-8").splitlines():
        digest, relative = row.split("  ", 1)
        expected_sources[relative] = digest
    if report.get("source_sha256") != expected_sources:
        raise ValueError("full-body report source inventory differs from the manifest")
    lock = ROOT / "firmware/body/dependencies.lock"
    lock_bytes = lock.read_bytes()
    if report.get("source_lock_sha256") != hashlib.sha256(lock_bytes).hexdigest():
        raise ValueError("full-body report does not use the current formal dependency lock")
    if report.get("effective_lock_sha256") == report.get("source_lock_sha256"):
        raise ValueError("copied local component path was not isolated in the build lock")
    components = report.get("components", {})
    if components.get("actual_contents_match_lock") is not True or not components.get("components"):
        raise ValueError("full-body report does not verify pinned component contents")

    configurations = report.get("configurations")
    if not isinstance(configurations, list) or {item.get("enabled") for item in configurations} != {False, True}:
        raise ValueError("full-body report must cover observer-off and observer-on builds")
    # v2/16m.csv declares ota_0 as 6000 KiB (not 6 MiB). Keep this
    # assertion tied to the partition table's actual byte capacity.
    expected_app_partition = 6000 * 1024
    verified = []
    for item in configurations:
        enabled = item["enabled"]
        build = Path(item.get("build_directory", ""))
        if not build.is_absolute():
            raise ValueError("full-body build directories must be absolute")
        build = build.resolve()
        try:
            relative_build = build.relative_to((ROOT / "tmp").resolve())
        except ValueError as exc:
            raise ValueError("full-body build directory must remain under repository tmp/") from exc
        if (item.get("build_pass") is not True
                or item.get("config_value") is not enabled
                or item.get("observer_source_member") is not enabled
                or item.get("reproducible_build") is not True
                or (enabled and item.get("observer_elf_symbol_count", 0) <= 0)
                or (not enabled and item.get("observer_elf_symbol_count") != 0)):
            raise ValueError("full-body build does not match observer/reproducibility configuration")
        artifacts = item.get("artifacts", {})
        required = ("xiaozhi.bin", "xiaozhi.elf", "partition_table/partition-table.bin",
                    "bootloader/bootloader.bin", "expression_assets.bin", "srmodels/srmodels.bin")
        for name in required:
            recorded = artifacts.get(name, {})
            artifact = build / name
            if (not artifact.is_file() or artifact.stat().st_size != recorded.get("bytes")
                    or hashlib.sha256(artifact.read_bytes()).hexdigest() != recorded.get("sha256")):
                raise ValueError("full-body output missing or changed: " + name)
        app_bytes = artifacts["xiaozhi.bin"]["bytes"]
        if (item.get("app_partition_bytes") != expected_app_partition
                or item.get("app_partition_free_bytes") != expected_app_partition - app_bytes
                or app_bytes >= expected_app_partition):
            raise ValueError("full-body App size/partition margin is incorrect")
        sdkconfig = build / "sdkconfig"
        if not sdkconfig.is_file() or hashlib.sha256(sdkconfig.read_bytes()).hexdigest() != item.get("sdkconfig_sha256"):
            raise ValueError("full-body sdkconfig is missing, stale, or has the wrong hash")
        if "CONFIG_APP_REPRODUCIBLE_BUILD=y" not in sdkconfig.read_text(encoding="utf-8").splitlines():
            raise ValueError("full-body sdkconfig does not enable reproducible build metadata")
        config_json = build / "config/sdkconfig.json"
        if not config_json.is_file() or json.loads(config_json.read_text(encoding="utf-8")).get("APP_REPRODUCIBLE_BUILD") is not True:
            raise ValueError("effective full-body configuration is not reproducible")
        verified.append({"enabled": enabled, "build_directory": (Path("tmp") / relative_build).as_posix(),
                         "app_bytes": app_bytes,
                         "app_partition_free_bytes": item["app_partition_free_bytes"],
                         "xiaozhi_bin_sha256": artifacts["xiaozhi.bin"]["sha256"]})
    unchanged = report.get("unchanged_outputs", [])
    if set(unchanged) != {"partition_table/partition-table.bin", "bootloader/bootloader.bin",
                          "expression_assets.bin", "srmodels/srmodels.bin"}:
        raise ValueError("full-body report has an incomplete unchanged-output comparison")
    by_mode = {item["enabled"]: item for item in configurations}
    for name in unchanged:
        if by_mode[False]["artifacts"][name] != by_mode[True]["artifacts"][name]:
            raise ValueError("observer mode changed a non-App output: " + name)
    return {"path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "result": report["result"], "configurations": verified,
            "locked_component_count": components["count"],
            "non_app_outputs_identical": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        output = Path(args.output)
        if not output.is_absolute():
            output = (ROOT / output).resolve()
        relative = output.relative_to(ROOT.resolve())
        if not relative.parts or relative.parts[0] not in {"artifacts", "tmp"} or output.suffix != ".json":
            raise ValueError("output must be a JSON file inside artifacts/ or tmp/")
        changed = status_paths()
        firmware_changed = {path for path in changed if path.startswith("firmware/body/")}
        if firmware_changed != EXPECTED_FIRMWARE_PATHS:
            raise ValueError(f"unexpected firmware paths: {sorted(firmware_changed ^ EXPECTED_FIRMWARE_PATHS)}")
        integration = audit_integration_files()
        manifest = audit_manifest()
        contract = audit_contract()
        cpp_report = audit_cpp_report(ROOT / "tmp/e1-26-cpp-observer-report.json")
        minimal_build_report = audit_minimal_idf_report(ROOT / "tmp/e1-26-idf-preflight-report.json")
        full_body_report = audit_full_body_report(ROOT / "tmp/e1-26-full-body-report.json")
        report = {
            "schema": "deskbot.expression.e1_26_observation_audit/v2",
            "edition": "E1.26",
            "date": "2026-10-27",
            "firmware_baseline_commit": BASELINE_COMMIT,
            "result": "offline_observer_audit_and_locked_full_build_passed",
            "audit_pass": True,
            "deployment_eligible": False,
            "flash_authorized": False,
            "safety": {
                "hardware_accessed": False,
                "serial_accessed": False,
                "full_firmware_build_passed": True,
                "minimal_idf_build_passed": True,
                "device_written": False,
                "nvs_changed": False,
            },
            "scope": {
                "changed_firmware_paths": sorted(firmware_changed),
                "official_behavior_files_after_guard_removal": "baseline_equal",
                "observer_source_default_off": True,
                "read_only_only": True,
                "no_c61_change": True,
                "no_eaf_mapping_change": True,
            },
            "integration": integration,
            "manifest": manifest,
            "observer_contract": contract,
            "host_cpp_report": cpp_report,
            "minimal_idf_report": minimal_build_report,
            "full_body_build_report": full_body_report,
            "limitations": [
                "The host FreeRTOS stub and minimal build do not prove ESP32 SMP timing or LCD behavior.",
                "The full locked build verifies compilation, configuration and image sizes; it does not prove physical behavior.",
                "No device runtime calls were collected; the observer only claims SetEmotion call entry.",
            ],
            "next_gate": "independent_build_review_then_user_scheduled_hardware_validation",
        }
        write_json(output, report)
        print(f"{report['result']}: {relative.as_posix()}")
        print("Offline only; deployment_eligible=false; flash_authorized=false.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError,
            subprocess.CalledProcessError) as exc:
        print(f"E1.26 audit failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
