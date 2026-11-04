#!/usr/bin/env python3
"""Re-read local Octal builds, explain deltas, and audit exact-sector rollback.

No serial, reset, download, or Flash operation is provided by this entrypoint.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from protocol.storage_octal import NO_WRITE_SAFETY, compare_images, config_diff, parse_config, sha, validate_hash, validate_report

PUBLIC = ROOT / "artifacts/expression-octal-e1-39-20261030"
IDF = Path(r"D:\Espressif\frameworks\esp-idf-v5.5.5")
WORK = ROOT / "tmp/e139-octal-20261030"


def mode_delta(mib: int) -> dict:
    """Exact Kconfig dependency changes, including removed invisible choices."""
    pairs = {
        "ESPTOOLPY_FLASHMODE": ('"dio"', '"dout"'),
        "ESPTOOLPY_FLASHMODE_DIO": ("n", None),
        "ESPTOOLPY_FLASHMODE_DOUT": ("n", None),
        "ESPTOOLPY_FLASHMODE_OPI": (None, "y"),
        "ESPTOOLPY_FLASHMODE_QIO": ("y", None),
        "ESPTOOLPY_FLASHMODE_QOUT": ("n", None),
        "ESPTOOLPY_FLASH_MODE_AUTO_DETECT": ("y", "n"),
        "ESPTOOLPY_FLASH_SAMPLE_MODE_DTR": (None, "n"),
        "ESPTOOLPY_OCT_FLASH": ("n", "y"),
        "FLASHMODE_DIO": ("n", None), "FLASHMODE_DOUT": ("n", None),
        "FLASHMODE_QIO": ("y", None), "FLASHMODE_QOUT": ("n", None),
        "SPI_FLASH_HPM_AUTO": ("y", None), "SPI_FLASH_HPM_DC_AUTO": ("y", None),
        "SPI_FLASH_HPM_DC_DISABLE": ("n", None), "SPI_FLASH_HPM_DIS": ("n", None),
        "SPI_FLASH_HPM_ENA": ("n", None), "SPI_FLASH_HPM_ON": ("y", None),
        "SPI_FLASH_ROM_IMPL": ("n", None),
    }
    if mib == 32:
        pairs.update({"BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH": (None, "y"),
                      "BOOTLOADER_FLASH_NEEDS_32BIT_ADDR_QUAD_FLASH": ("y", None),
                      "BOOTLOADER_FLASH_NEEDS_32BIT_FEAT": ("y", None),
                      # IDF sdkconfig.rename keeps this legacy symbol in the
                      # text snapshot while generated JSON exposes the new
                      # BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH name.
                      "SPI_FLASH_OCTAL_32BIT_ADDR_ENABLE": (None, "y")})
    elif mib != 16:
        raise ValueError("only the reviewed capacities are allowed")
    return {"CONFIG_" + key: {"old": old, "candidate": new} for key, (old, new) in pairs.items()}


def capacity_delta() -> dict:
    return {
        "CONFIG_BOOTLOADER_FLASH_32BIT_ADDR": {"old": None, "candidate": "y"},
        "CONFIG_BOOTLOADER_CACHE_32BIT_ADDR_OCTAL_FLASH": {"old": None, "candidate": "y"},
        "CONFIG_SPI_FLASH_OCTAL_32BIT_ADDR_ENABLE": {"old": None, "candidate": "y"},
        "CONFIG_ESPTOOLPY_FLASHSIZE": {"old": '"16MB"', "candidate": '"32MB"'},
        "CONFIG_ESPTOOLPY_FLASHSIZE_16MB": {"old": "y", "candidate": "n"},
        "CONFIG_ESPTOOLPY_FLASHSIZE_32MB": {"old": "n", "candidate": "y"},
    }


def normalized_config(text: str) -> dict:
    return parse_config(text)


def raw_json_delta(old: dict, new: dict) -> dict:
    return {key: {"old": old.get(key), "candidate": new.get(key)}
            for key in sorted(old.keys() | new.keys()) if old.get(key) != new.get(key)}


def expected_generated_delta(mib: int) -> dict:
    def value(text):
        if text in (None, "n", "y"):
            return {None: None, "n": False, "y": True}[text]
        return json.loads(text)
    return {key.removeprefix("CONFIG_"): {side: value(text) for side, text in change.items()}
            for key, change in mode_delta(mib).items() if not key.startswith("CONFIG_FLASHMODE_")
            and key != "CONFIG_SPI_FLASH_OCTAL_32BIT_ADDR_ENABLE"}


def source_anchors() -> list[dict]:
    requests = (
        ("components/esptool_py/Kconfig.projbuild", "config ESPTOOLPY_FLASH_MODE_AUTO_DETECT", 24),
        ("components/esptool_py/Kconfig.projbuild", "choice ESPTOOLPY_FLASH_SAMPLE_MODE", 26),
        ("components/bootloader/Kconfig.projbuild", "config BOOTLOADER_FLASH_32BIT_ADDR", 33),
        ("components/bootloader/sdkconfig.rename", "CONFIG_SPI_FLASH_OCTAL_32BIT_ADDR_ENABLE", 1),
        ("components/spi_flash/Kconfig", "choice SPI_FLASH_HPM", 33),
        ("components/spi_flash/Kconfig", "config SPI_FLASH_ROM_IMPL", 18),
        ("components/spi_flash/esp_flash_spi_init.c", "#if CONFIG_ESPTOOLPY_OCT_FLASH", 15),
        ("components/spi_flash/flash_mmap.c", "#if !CONFIG_BOOTLOADER_CACHE_32BIT_ADDR_QUAD_FLASH", 11),
        ("components/bootloader_support/bootloader_flash/src/bootloader_flash_config_esp32s3.c",
         "bootloader_flash_32bits_address_map_enable(bootloader_flash_get_spi_mode());", 9),
        ("components/bootloader_support/bootloader_flash/src/bootloader_flash.c",
         "void bootloader_flash_32bits_address_map_enable", 54),
        ("components/spi_flash/spi_flash_chip_mxic_opi.c", "spi_flash_caps_t spi_flash_chip_mxic_opi_get_caps", 9),
    )
    records = []
    for relative, token, count in requests:
        raw = (IDF / relative).read_bytes()
        lines = raw.decode("utf-8").splitlines()
        hits = [index for index, line in enumerate(lines) if token in line]
        if not hits:
            raise ValueError("reviewed source anchor missing: " + relative)
        start = hits[0]
        records.append({"path": relative, "sha256": sha(raw), "anchor": token,
                        "start_line": start + 1, "end_line": min(len(lines), start + count),
                        "excerpt": "\n".join(lines[start:start + count])})
    return records


def elf_facts(raw: bytes) -> dict:
    # pyelftools is an ESP-IDF build dependency; portable tests do not require it.
    from elftools.elf.elffile import ELFFile
    elf = ELFFile(io.BytesIO(raw))
    sections = []
    for section in elf.iter_sections():
        if section["sh_flags"] & 2:  # SHF_ALLOC, including runtime zero-fill size.
            sections.append({"name": section.name, "address": section["sh_addr"],
                             "bytes": section["sh_size"], "type": section["sh_type"],
                             "sha256": sha(section.data()) if section["sh_type"] != "SHT_NOBITS" else None})
    symbols = {}
    table = elf.get_section_by_name(".symtab")
    if table:
        for symbol in table.iter_symbols():
            if symbol["st_info"]["type"] == "STT_FUNC" and symbol["st_size"]:
                symbols.setdefault(symbol.name, []).append({"address": symbol["st_value"], "bytes": symbol["st_size"]})
    return {"bytes": len(raw), "sha256": sha(raw), "entry": elf["e_entry"],
            "sections": sections, "function_geometry": symbols}


def compare_elf(old: Path, new: Path) -> dict:
    a, b = elf_facts(old.read_bytes()), elf_facts(new.read_bytes())
    sa = a.pop("function_geometry")
    sb = b.pop("function_geometry")
    changed = {key: {"control": sa.get(key), "candidate": sb.get(key)}
               for key in sorted(sa.keys() | sb.keys()) if sa.get(key) != sb.get(key)}
    return {"control": a, "candidate": b, "byte_identical": old.read_bytes() == new.read_bytes(),
            "function_geometry_changed_count": len(changed),
            "flash_related_functions": {key: value for key, value in changed.items()
                                        if any(token in key.lower() for token in ("flash", "mmap", "cache", "spi", "bootloader"))},
            "interpretation": "Addresses/sizes and section hashes are static evidence; relocated bytes do not establish behavioral equivalence."}


def validate_public_review(build: dict, evidence: dict) -> None:
    """Metadata consistency only; the local --check re-reads private evidence."""
    validate_report(build)
    if (evidence.get("schema") != "deskbot.n32r16.explicit_octal_review.v1"
            or evidence.get("edition") != "E1.39"
            or evidence.get("result") != "explicit_octal_config_images_elf_and_rollback_reviewed_runtime_pending"
            or evidence.get("safety") != NO_WRITE_SAFETY
            or evidence.get("protected_outputs_match_e137") is not True
            or any(evidence.get(key) is not False for key in (
                "deployment_eligible", "flash_authorized", "runtime_verified", "hardware_accepted", "live_snapshot_current"))):
        raise ValueError("public E1.39 review crossed its static/offline boundary")
    configs = evidence.get("config_deltas", {})
    if configs.get("16m_to_32m") != capacity_delta():
        raise ValueError("public review contains unreviewed capacity configuration changes")
    for mib in (16, 32):
        row = configs.get(f"{mib}m_vs_e137", {})
        if (row.get("sdkconfig") != mode_delta(mib)
                or row.get("generated") != {target: expected_generated_delta(mib) for target in ("app", "bootloader")}):
            raise ValueError("public review contains unreviewed Octal dependency changes")
    images = evidence.get("capacity_image_pair", {})
    if set(images) != {"xiaozhi.bin", "bootloader/bootloader.bin"}:
        raise ValueError("public paired-image review is incomplete")
    for name, comparison in images.items():
        for row, key in zip(build["builds"], ("control", "candidate")):
            facts = comparison[key]
            if (facts != row["app_image" if name == "xiaozhi.bin" else "bootloader_image"]
                    or {"bytes": facts.get("bytes"), "sha256": facts.get("sha256")} != row["artifacts"][name]
                    or facts.get("configured_flash_bytes") != row["configured_flash_bytes"]
                    or facts.get("checksum_verified") is not True or facts.get("appended_sha256_verified") is not True):
                raise ValueError("public paired-image metadata differs from archived build")
    if set(evidence.get("elf_checks", {})) != {"app_vs_e137_32m", "app_16m_to_32m",
                                              "bootloader_16m_to_32m", "bootloader_vs_e137_32m"}:
        raise ValueError("public ELF comparison is incomplete")
    for key, comparison in evidence["elf_checks"].items():
        name = "bootloader/bootloader.elf" if key.startswith("bootloader_") else "xiaozhi.elf"
        for side in ("control", "candidate") if "16m_to_32m" in key else ("candidate",):
            artifact = build["builds"][0 if side == "control" else 1]["artifacts"][name]
            facts = comparison.get(side, {})
            if {"bytes": facts.get("bytes"), "sha256": facts.get("sha256")} != artifact:
                raise ValueError("public ELF identity differs from archived build")
        if type(comparison.get("function_geometry_changed_count")) is not int or comparison["function_geometry_changed_count"] < 0:
            raise ValueError("invalid ELF comparison geometry")
    anchors = evidence.get("source_anchors", [])
    if len(anchors) != 11:
        raise ValueError("reviewed source anchors are missing")
    for anchor in anchors:
        validate_hash(anchor.get("sha256"))
        if (not isinstance(anchor.get("excerpt"), str) or not anchor["excerpt"]
                or not isinstance(anchor.get("anchor"), str)
                or anchor["anchor"] not in anchor["excerpt"].split("\n")[0]
                or type(anchor.get("start_line")) is not int or type(anchor.get("end_line")) is not int
                or anchor["start_line"] < 1
                or anchor["end_line"] - anchor["start_line"] + 1 != len(anchor["excerpt"].split("\n"))):
            raise ValueError("public source anchor extent is inconsistent")


def review(report: dict) -> dict:
    validate_report(report)
    expected_work = ROOT / report["work_dir"]
    if expected_work != WORK:
        raise ValueError("unreviewed private build directory")
    reference = json.loads((ROOT / "artifacts/expression-capacity-build-e1-37b-20261030/build-report.json").read_bytes())
    source_sha = sha((ROOT / "firmware/body/SOURCE_MANIFEST.sha256").read_bytes())
    if report["source_manifest_sha256"] != source_sha or source_sha != reference["source_manifest_sha256"]:
        raise ValueError("formal source did not remain at the reviewed E1.26 boundary")
    deltas = {}
    for old, new, mib in zip(reference["builds"], report["builds"], (16, 32)):
        a, b = ROOT / old["archive"], ROOT / new["archive"]
        diff = config_diff(normalized_config((a / "sdkconfig").read_text(encoding="utf-8")),
                           normalized_config((b / "sdkconfig").read_text(encoding="utf-8")))
        if diff != mode_delta(mib):
            raise ValueError("effective sdkconfig changed beyond reviewed Octal dependencies: " + json.dumps(diff))
        generated_diffs = {}
        # Check the entire generated config for both subprojects, rather than
        # allowing only the hand-picked display keys to hide other changes.
        for target, filename in (("app", "config/sdkconfig.json"), ("bootloader", "bootloader/config/sdkconfig.json")):
            ja, jb = json.loads((a / filename).read_bytes()), json.loads((b / filename).read_bytes())
            raw_diff = raw_json_delta(ja, jb)
            if raw_diff != expected_generated_delta(mib):
                raise ValueError("generated App/bootloader config contains unreviewed deltas")
            generated_diffs[target] = raw_diff
        deltas[f"{mib}m_vs_e137"] = {"sdkconfig": diff, "generated": generated_diffs}
    a, b = [ROOT / row["archive"] for row in report["builds"]]
    diff = config_diff(normalized_config((a / "sdkconfig").read_text(encoding="utf-8")),
                       normalized_config((b / "sdkconfig").read_text(encoding="utf-8")))
    if diff != capacity_delta():
        raise ValueError("explicit-Octal 16/32 pair changed outside the capacity dependencies")
    deltas["16m_to_32m"] = diff
    image_pair = {name: compare_images((a / name).read_bytes(), (b / name).read_bytes())
                  for name in ("xiaozhi.bin", "bootloader/bootloader.bin")}
    old32 = ROOT / reference["builds"][1]["archive"]
    old_boot_elf = ROOT / "tmp/e137b-capacity/o/bootloader/bootloader.elf"
    elf_checks = {"app_vs_e137_32m": compare_elf(old32 / "xiaozhi.elf", b / "xiaozhi.elf"),
                  "app_16m_to_32m": compare_elf(a / "xiaozhi.elf", b / "xiaozhi.elf"),
                  "bootloader_16m_to_32m": compare_elf(a / "bootloader/bootloader.elf", b / "bootloader/bootloader.elf")}
    if old_boot_elf.is_file():
        elf_checks["bootloader_vs_e137_32m"] = compare_elf(old_boot_elf, b / "bootloader/bootloader.elf")
    result = {"schema": "deskbot.n32r16.explicit_octal_review.v1", "edition": "E1.39", "date": "2026-10-30",
            "result": "explicit_octal_config_images_elf_and_rollback_reviewed_runtime_pending",
            "build_report_sha256": sha((PUBLIC / "report.json").read_bytes()),
            "review_script_sha256": sha(Path(__file__).read_bytes()),
            "config_deltas": deltas, "capacity_image_pair": image_pair,
            "elf_checks": elf_checks, "source_anchors": source_anchors(),
            "protected_outputs_match_e137": True, "live_snapshot_current": False,
            "safety": dict(NO_WRITE_SAFETY), "deployment_eligible": False,
            "flash_authorized": False, "runtime_verified": False, "hardware_accepted": False}
    validate_public_review(report, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "scripts/build-expression-e1-39-octal.py"), "--check"], cwd=ROOT, check=True)
    expected = review(json.loads((PUBLIC / "report.json").read_bytes()))
    output = PUBLIC / "review.json"
    if args.check:
        if json.loads(output.read_bytes()) != expected:
            raise ValueError("stale E1.39 review metadata")
    else:
        output.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("E1.39 entire-config, image, ELF, source and exact rollback audit passed; runtime/hardware pending.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("E1.39 review blocked: " + str(error), file=sys.stderr)
        raise SystemExit(2)
