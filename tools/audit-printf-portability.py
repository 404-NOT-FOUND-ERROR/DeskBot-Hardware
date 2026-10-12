#!/usr/bin/env python3
"""Detect newly introduced wide integer printf formats under nano libc.

This is a source-format gate only; it does not validate varargs types or runtime
behaviour. It never changes source files or sdkconfig.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys

NANO_OPTIONS = ("CONFIG_LIBC_NEWLIB_NANO_FORMAT", "CONFIG_NEWLIB_NANO_FORMAT")
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
LEXEME = re.compile(r'(?P<string>(?:u8|u|U|L)?"(?:\\.|[^"\\])*")|(?P<char>\'(?:\\.|[^\'\\])*\')|(?P<comment>//[^\n]*|/\*.*?\*/)', re.DOTALL)
STRING = re.compile(r'(?:u8|u|U|L)?"(?:\\.|[^"\\])*"', re.DOTALL)
WIDE_FORMAT = re.compile(r"%(?:\d+\$)?[-+ #0']*(?:\d+|\*(?:\d+\$)?)?(?:\.(?:\d+|\*(?:\d+\$)?))?(?:ll|j|I64)[diouxXn]")
WIDE_MACRO = re.compile(r"\bPRI[diouxX](?:64|MAX)\b")


def without_comments(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return match.group() if match.lastgroup != "comment" else "".join("\n" if c == "\n" else " " for c in match.group())
    return LEXEME.sub(replace, text)


def detect(text: str, path: str) -> list[dict]:
    clean = without_comments(text)
    hits = []
    for token in STRING.finditer(clean):
        value = token.group()
        specs = WIDE_FORMAT.findall(value.replace("%%", ""))
        if specs:
            hits.append({"path": path, "line": text.count("\n", 0, token.start()) + 1, "kind": "wide_integer_format", "formats": specs, "literal": value})
    code = STRING.sub(lambda m: "".join("\n" if c == "\n" else " " for c in m.group()), clean)
    for macro in WIDE_MACRO.finditer(code):
        hits.append({"path": path, "line": text.count("\n", 0, macro.start()) + 1, "kind": "wide_integer_macro", "formats": [macro.group()], "literal": macro.group()})
    return hits


def nano_configuration(text: str) -> dict:
    values = {}
    for option in NANO_OPTIONS:
        if re.search(rf"^{option}=y$", text, re.MULTILINE): values[option] = True
        elif re.search(rf"^# {option} is not set$", text, re.MULTILINE): values[option] = False
    return {"nano_enabled": any(values.values()) if values else None, "options": values}


def signature(hit: dict) -> tuple:
    return hit["path"], hit["kind"], hit["literal"]


def classify(baseline_hits: list[dict], candidate_hits: list[dict], nano: bool | None) -> dict:
    existing = Counter(signature(hit) for hit in baseline_hits)
    preexisting, introduced = [], []
    for hit in candidate_hits:
        if existing[signature(hit)]: existing[signature(hit)] -= 1; preexisting.append(hit)
        else: introduced.append(hit)
    blocked = introduced if nano is not False else []
    return {"status": "fail" if blocked else "pass", "new_blocking": blocked, "new_nonblocking": introduced if nano is False else [], "preexisting": preexisting, "reason": "nano_libc_does_not_support_64_bit_integer_formats" if nano is True else "nano_configuration_unknown" if nano is None else "full_formatting_enabled"}


def source_paths(root: Path, scopes: list[str]) -> list[str]:
    def listed(options: list[str]) -> list[str]:
        result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *options, "--", *scopes], check=True, capture_output=True)
        return [p for p in result.stdout.decode("utf-8").split("\0") if p]
    paths = set(listed([]) + listed(["--others", "--exclude-standard"]))
    return sorted(p for p in paths if Path(p).suffix.lower() in SOURCE_SUFFIXES)


def inspect(root: Path, paths: list[str]) -> list[dict]:
    hits = []
    for relative in paths:
        file = root / relative
        if file.is_file(): hits.extend(detect(file.read_text(encoding="utf-8"), relative))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--sdkconfig", required=True, type=Path)
    parser.add_argument("--path", action="append", dest="scopes")
    args = parser.parse_args()
    scopes = args.scopes or ["firmware/body/main/boards/esp_vocat"]
    config = nano_configuration(args.sdkconfig.read_text(encoding="utf-8"))
    before = source_paths(args.baseline, scopes); after = source_paths(args.candidate, scopes)
    report = {"schema": "deskbot.printf-portability-audit.v1", "baseline": str(args.baseline), "candidate": str(args.candidate), "sdkconfig": str(args.sdkconfig), "scopes": scopes, "baseline_source_count": len(before), "candidate_source_count": len(after), "configuration": config, "limit": "Source gate only; does not validate varargs types, all libc behavior or runtime safety"}
    report.update(classify(inspect(args.baseline, before), inspect(args.candidate, after), config["nano_enabled"]))
    print(json.dumps(report, ensure_ascii=False, indent=2)); return 2 if report["status"] == "fail" else 0


if __name__ == "__main__": sys.exit(main())
