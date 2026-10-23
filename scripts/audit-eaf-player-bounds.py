#!/usr/bin/env python3
"""E1.3c: native host guard tests of the HASH-ANCHORED production C decoder.

No firmware builds, shared component edits, serial I/O, or deployable packages.
Only controlled fixtures run in the child process; this is NOT a fuzz sandbox.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from protocol.eaf_compression import encode_block, huffman_decode, huffman_encode
from protocol.eaf_preview import _rle_decode

spec = importlib.util.spec_from_file_location("e13c_compression_anchor", ROOT / "scripts/audit-expression-compression.py")
assert spec and spec.loader
ANCHOR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ANCHOR)
PLAYER = ANCHOR.PLAYER_ROOT
OUTPUT = ROOT / "tmp/expression-e1-3c-20261020"
HARNESS = ROOT / "tests/eaf_player_host"
COMPILER = ROOT / "tmp/zig-x86_64-windows-0.15.2/zig.exe"
CANDIDATE_4 = ROOT / "tmp/expression-e1-3b-20261020/candidate/shy-quantized-4bit-15color-host-only-do-not-flash.eaf"
CANDIDATE_8 = ROOT / "tmp/expression-e1-3b-20261020/candidate/shy-smallest-lossless-8bit-device-playback-unverified-do-not-flash.eaf"
SHA4 = "7144c594e066c2411e43a492c7ff8f7c4befb0ac282f05250a0656f1b637c9fd"
SHA8 = "916d56e048525bc68a38f4b14afab54369d2556ef6a1362323dcbccc9ac598cf"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_output(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to((ROOT / "tmp").resolve()) or resolved == (ROOT / "tmp").resolve():
        raise ValueError("E1.3c output must be a stage directory inside project tmp")
    return resolved


def immutable(path: Path, data: bytes) -> None:
    if path.exists() and path.read_bytes() != data:
        raise ValueError(f"refusing to overwrite different evidence: {path}")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class ProbeResult(ctypes.Structure):
    _fields_ = [("result", ctypes.c_int32)] + [(name, ctypes.c_uint32) for name in (
        "prefix_changed", "suffix_changed", "internal_changed", "first_overrun",
        "last_overrun", "internal_last_overrun", "live_allocations", "callback_output_bytes",
        "logical_capacity", "advertised_capacity")]


class NativeDecoder:
    def __init__(self, dll: Path):
        self.lib = ctypes.CDLL(str(dll))
        self.lib.deskbot_eaf_probe.argtypes = [ctypes.c_uint32] * 3 + [ctypes.c_void_p] + [ctypes.c_uint32] * 4 + [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ProbeResult)]
        self.lib.deskbot_eaf_probe.restype = ctypes.c_int
        self.lib.deskbot_eaf_palette.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
        self.lib.deskbot_eaf_palette.restype = ctypes.c_int

    def probe(self, block: bytes, *, width: int, height: int, depth: int,
              fill: int = 0x36, poison: int = 0x11, mode: int = 0) -> tuple[dict, bytes]:
        if not block or len(block) > 65536 or not 0 < width <= 1024 or not 0 < height <= 1024:
            raise ValueError("controlled host fixture exceeds supported limits")
        if depth not in (4, 8) or mode not in (0, 1):
            raise ValueError("unsupported host probe mode or depth")
        capacity = width * (height + height % 2) // 2 if depth == 4 else width * height
        source = ctypes.create_string_buffer(block)
        out = (ctypes.c_uint8 * capacity)()
        report = ProbeResult()
        ret = self.lib.deskbot_eaf_probe(width, height, depth, source, len(block), fill, poison,
                                        mode, out, capacity, ctypes.byref(report))
        if ret:
            raise RuntimeError(f"native harness failed: {ret}")
        return {name: getattr(report, name) for name, _ in report._fields_}, bytes(out)

    def colors(self, palette: bytes) -> list[bytes]:
        count = len(palette) // 4
        source = ctypes.create_string_buffer(palette)
        colors = (ctypes.c_uint16 * count)()
        transparent = (ctypes.c_uint8 * count)()
        if self.lib.deskbot_eaf_palette(source, count, colors, transparent):
            raise RuntimeError("native palette conversion failed")
        result = []
        for color, alpha in zip(colors, transparent):
            r, g, b = (color >> 11) & 31, (color >> 5) & 63, color & 31
            result.append(b"\0\0\0\0" if alpha else bytes(((r << 3) | (r >> 2),
                          (g << 2) | (g >> 4), (b << 3) | (b >> 2), 255)))
        return result


def frames(raw: bytes):
    if len(raw) < 16 or raw[:4] != b"\x89EAF":
        raise ValueError("bad anchored EAF header")
    count, checksum, length = struct.unpack_from("<III", raw, 4)
    start = 16 + count * 8
    if not 0 < count <= 4096 or length != len(raw) - 16 or start > len(raw) or sum(raw[16:]) & 0xffffffff != checksum:
        raise ValueError("bad anchored EAF table/checksum")
    for index in range(count):
        size, offset = struct.unpack_from("<II", raw, 16 + index * 8)
        if size < 20 or start + offset + size > len(raw):
            raise ValueError("bad image frame range")
        frame = raw[start + offset:start + offset + size]
        if frame[:4] != b"ZZ_S":
            raise ValueError("this audit accepts image-only controlled candidates")
        content = frame[2:]
        depth = content[9]
        w, h, blocks, bh = struct.unpack_from("<HHHH", content, 10)
        if (depth not in (4, 8) or not 0 < w <= 1024 or not 0 < h <= 1024
                or not 0 < blocks <= 256 or not 0 < bh <= 1024 or w % 2 and depth == 4
                or not (blocks - 1) * bh < h <= blocks * bh):
            raise ValueError("unsupported image geometry")
        palette_start = 18 + blocks * 4
        palette_end = palette_start + 4 * (1 << depth)
        if palette_end > len(content):
            raise ValueError("truncated block table or palette")
        lengths = struct.unpack_from("<" + "I" * blocks, content, 18)
        if any(n < 2 for n in lengths) or palette_end + sum(lengths) != len(content):
            raise ValueError("bad block ranges")
        cursor = palette_end
        data = []
        for n in lengths:
            data.append(content[cursor:cursor + n])
            cursor += n
        yield index, w, h, depth, bh, content[palette_start:palette_end], data


def safe_decode(block: bytes, expected: int, valid: int) -> bytes:
    if block[0] == 0:
        return _rle_decode(block[1:], expected, "C harness reference", valid)
    if block[0] == 1:
        rle = huffman_decode(block[1:], expected * 2)
        return _rle_decode(rle, expected, "C harness reference", valid)
    if block[0] == 3:
        decoded = huffman_decode(block[1:], expected)
        if len(decoded) not in (expected, valid):
            raise ValueError("bad direct Huffman length")
        return decoded
    raise ValueError("controlled candidate uses unsupported encoding")


def clean(report: dict) -> bool:
    return not any(report[key] for key in ("prefix_changed", "suffix_changed", "internal_changed", "live_allocations"))


def audit_animation(native: NativeDecoder, raw: bytes) -> dict:
    hashes, encodings, allocations, output_sizes = [], Counter(), set(), set()
    blocks_total = partial_blocks = probes = 0
    for _, w, h, depth, bh, palette, blocks in frames(raw):
        rgba = bytearray()
        colors = native.colors(palette)
        for number, block in enumerate(blocks):
            rows = min(bh, h - number * bh)
            nominal = w * bh // (2 if depth == 4 else 1)
            valid = w * rows // (2 if depth == 4 else 1)
            expected = safe_decode(block, nominal, valid)
            encodings[block[0]] += 1
            blocks_total += 1
            partial_blocks += rows != bh
            actual = b""
            for fill in (0x36, 0xc9):
                for mode in (0, 1):
                    report, data = native.probe(block, width=w, height=bh, depth=depth, fill=fill, mode=mode)
                    probes += 1
                    if report["result"] or not clean(report) or data[:len(expected)] != expected:
                        raise AssertionError(f"candidate C decode/guard failure at block {number}: {report}")
                    if data[len(expected):] != bytes((fill,)) * (len(data) - len(expected)):
                        raise AssertionError("C decoder wrote past expected packed output")
                    if mode and report["callback_output_bytes"] != len(expected):
                        raise AssertionError("registered callback reported different decoded length")
                    allocations.add(report["logical_capacity"])
                    output_sizes.add(len(expected))
                    actual = data[:valid]
            if depth == 4:
                actual = bytes(value for packed in actual for value in (packed >> 4, packed & 15))
            rgba.extend(b"".join(colors[value] for value in actual))
        if len(rgba) != w * h * 4:
            raise AssertionError("C-decoded image has incorrect size")
        hashes.append(digest(rgba))
    return {
        "frame_count": len(hashes), "decoded_frame_sha256": hashes,
        "block_count_including_repeated_frames": blocks_total,
        "partial_last_blocks": partial_blocks, "native_probe_calls": probes,
        "actual_encoding_counts": dict(sorted(encodings.items())),
        "logical_allocation_bytes": sorted(allocations),
        "callback_output_bytes": sorted(output_sizes),
        "all_block_api_outputs_equal_bounded_reference": True,
        "both_initialization_patterns_passed": True,
        "output_and_internal_heap_canaries_intact": True,
        "nominal_output_tail_unchanged": True,
        "palette_conversion": "production eaf_palette_get_color to RGB565, expanded to RGBA on host",
        "scope": "block decoding and palette only; not full gfx_anim clipping/timing/LCD",
    }


def adversarial_cases() -> list[dict]:
    def case(name, block, category, depth=4, width=8, height=4):
        return dict(id=name, block=block, category=category, depth=depth, width=width, height=height)
    single = b"\x03" + struct.pack("<H", 4) + b"\x00\x22\x01\x00"
    return [
        case("valid_rle_4bit", encode_block(bytes((0x12,)) * 16, 0), "valid"),
        case("valid_huffman_rle_4bit", encode_block(bytes((0x12,)) * 16, 1), "valid"),
        case("valid_direct_huffman_4bit", encode_block(bytes((0x12,)) * 16, 3), "valid"),
        case("single_symbol_explicit_stream", encode_block(bytes(16), 3), "valid"),
        case("odd_block_height_packed", encode_block(bytes(range(12)), 1), "valid", height=3),
        case("short_last_block", encode_block(bytes((0x12,)) * 4, 0), "short_output"),
        case("rle_rejects_over_advertised", b"\x00\x21\x22", "bad_input"),
        case("unknown_encoding", b"\xff\x00", "bad_input"),
        case("truncated_huffman_dictionary", b"\x03\x08\x00\x00", "bad_input"),
        case("rle_writes_between_packed_and_advertised", b"\x00\x18\x22", "overflow"),
        case("huffman_rle_writes_between_packed_and_advertised", encode_block(bytes((0x22,)) * 24, 1), "overflow"),
        case("raw_writes_between_packed_and_advertised", b"\x05" + bytes((0x22,)) * 24, "overflow"),
        case("direct_huffman_empty_single_symbol", single, "overflow"),
        case("direct_huffman_over_packed", encode_block(bytes((0x22,)) * 24, 3), "overflow"),
        case("direct_huffman_checks_after_write", encode_block(bytes((0x22,)) * 40, 3), "overflow"),
        case("direct_huffman_8bit_checks_after_write", encode_block(bytes((0x22,)) * 40, 3), "overflow", depth=8),
        case("huffman_rle_internal_write_before_check", b"\x01" + huffman_encode(bytes((0x22,)) * 80), "internal_overflow"),
        case("odd_rle_reads_beyond_declared_input", b"\x00\x10", "read_past_length"),
        case("empty_huffman_dictionary_no_write_success", b"\x03\x01\x00\x00", "uninitialized_success"),
    ]


def audit_adversarial(native: NativeDecoder) -> list[dict]:
    results = []
    for fixture in adversarial_cases():
        observations, outputs = [], []
        for poison in (0x11, 0x22):
            report, data = native.probe(fixture["block"], width=fixture["width"], height=fixture["height"],
                                        depth=fixture["depth"], poison=poison)
            observations.append(report)
            outputs.append(data)
            if fixture["category"] in ("valid", "short_output"):
                capacity = fixture["width"] * fixture["height"] // (2 if fixture["depth"] == 4 else 1)
                expected = safe_decode(fixture["block"], capacity,
                                       4 if fixture["category"] == "short_output" else capacity)
                if (data[:len(expected)] != expected
                        or data[len(expected):] != bytes((0x36,)) * (len(data) - len(expected))):
                    raise AssertionError(f"valid native fixture output mismatch: {fixture['id']}")
        result = {key: value for key, value in fixture.items() if key != "block"}
        result.update(block_hex=fixture["block"].hex(), observations=observations,
                      input_tail_poison_changes_output=outputs[0] != outputs[1],
                      output_sha256=[digest(data) for data in outputs],
                      returned_success=observations[0]["result"] == 0,
                      canaries_intact=all(clean(item) for item in observations))
        results.append(result)
    validate_adversarial_results(results)
    return results


def validate_adversarial_results(results: list[dict]) -> None:
    by_id = {row["id"]: row for row in results}
    expected_ids = {row["id"] for row in adversarial_cases()}
    if len(by_id) != len(results) or set(by_id) != expected_ids:
        raise AssertionError("native boundary report has missing, duplicate or unexpected cases")
    for fixture in adversarial_cases():
        row = by_id[fixture["id"]]
        category = fixture["category"]
        if row["category"] != category or len(row["observations"]) != 2:
            raise AssertionError(f"native fixture metadata mismatch: {row}")
        successes = [item["result"] == 0 for item in row["observations"]]
        if row["returned_success"] != successes[0] or row["canaries_intact"] != all(clean(item) for item in row["observations"]):
            raise AssertionError(f"native fixture summary disagrees with observations: {row}")
        if category == "valid" and (not all(successes) or not row["canaries_intact"]):
            raise AssertionError(f"valid native control failed: {row}")
        if category == "short_output" and (
                not all(successes) or not row["canaries_intact"]):
            raise AssertionError(f"short controlled output did not remain within the allocation: {row}")
        if category == "bad_input" and (
                any(successes) or not row["canaries_intact"]):
            raise AssertionError(f"malformed native control was not rejected cleanly: {row}")
        if category == "overflow" and not all(item["suffix_changed"] for item in row["observations"]):
            raise AssertionError(f"expected original decoder overflow was not reproduced: {row}")
        if category == "internal_overflow" and not all(item["internal_changed"] for item in row["observations"]):
            raise AssertionError(f"internal Huffman overflow not reproduced: {row}")
        if category == "read_past_length" and not row["input_tail_poison_changes_output"]:
            raise AssertionError(f"expected read-past-length was not reproduced: {row}")
        if category == "uninitialized_success":
            capacity = row["observations"][0]["logical_capacity"]
            if (not all(successes) or not row["canaries_intact"]
                    or row["output_sha256"] != [digest(bytes((0x36,)) * capacity)] * 2):
                raise AssertionError(f"expected no-write success behavior changed: {row}")


def worker(dll: Path, result_path: Path) -> None:
    native = NativeDecoder(dll)
    animations = {}
    for name, path, expected_sha in (("candidate_4bit", CANDIDATE_4, SHA4),
                                     ("control_8bit", CANDIDATE_8, SHA8),
                                     ("e1_2_reference", ANCHOR.SHY_EAF, ANCHOR.EXPECTED_SHY_SHA256)):
        raw = path.read_bytes()
        if digest(raw) != expected_sha:
            raise ValueError(f"candidate anchor mismatch: {path}")
        animations[name] = dict(path=str(path.relative_to(ROOT)).replace("\\", "/"), sha256=expected_sha,
                                **audit_animation(native, raw))
    reference = animations["e1_2_reference"]["decoded_frame_sha256"]
    for value in animations.values():
        value["c_frame_sha256_sequence_matches_e1_2_reference"] = value["decoded_frame_sha256"] == reference
        if not value["c_frame_sha256_sequence_matches_e1_2_reference"]:
            raise AssertionError("C output differs from visually accepted E1.2")
    result = {"animations": animations, "controlled_adversarial_cases": audit_adversarial(native)}
    immutable(result_path, (json.dumps(result, indent=2) + "\n").encode())


def build_and_audit(output: Path, compiler: Path) -> dict:
    output = safe_output(output)
    # Read-only identity checks precede any C compilation or candidate execution.
    before = ANCHOR._verify_player_anchor()
    for path, sha in ((CANDIDATE_4, SHA4), (CANDIDATE_8, SHA8)):
        if digest(path.read_bytes()) != sha:
            raise ValueError(f"candidate anchor mismatch: {path}")
    if not compiler.is_file():
        raise FileNotFoundError("existing host Zig compiler missing; no automatic download")
    version = subprocess.run([str(compiler), "version"], capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    output.mkdir(parents=True, exist_ok=True)
    dll = output / "eaf_bounds_harness.dll"
    # Zig caches also remain in this stage, never D:\ root or global temp.
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(output / "cache-local"),
               ZIG_GLOBAL_CACHE_DIR=str(output / "cache-global"))
    command = [str(compiler), "cc", "-std=c11", "-O1", "-Wall", "-Wextra",
               "-I", str(HARNESS), "-I", str(PLAYER / "include"),
               "-I", str(PLAYER / "src/lib/eaf"), "-shared", "-fPIC",
               str(HARNESS / "eaf_bounds_harness.c"), "-o", str(dll)]
    compiled = subprocess.run(command, capture_output=True, text=True, env=env, timeout=180, cwd=output)
    immutable(output / "compile.log", (compiled.stdout + compiled.stderr).encode())
    if compiled.returncode:
        raise RuntimeError(f"host C compilation failed; see {output / 'compile.log'}")
    child_result = output / "native-results.json"
    child = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                            "--dll", str(dll), "--result", str(child_result)],
                           capture_output=True, text=True, timeout=180, cwd=output)
    immutable(output / "native-run.log", (child.stdout + child.stderr).encode())
    if child.returncode:
        raise RuntimeError(f"isolated native tests failed ({child.returncode}); see native-run.log")
    native = json.loads(child_result.read_text())
    after = ANCHOR._verify_player_anchor()
    if before != after:
        raise AssertionError("read-only player/device artifact anchor changed during audit")
    results = {
        "schema": "deskbot.eaf_player_bounds_audit/v1",
        "result": "controlled_candidate_decode_passes_general_decoder_bounds_fail",
        "hardware_accessed": False, "firmware_changed": False,
        "firmware_built": False, "device_package_eligible": False,
        "toolchain": {"name": "existing Zig native host cc", "version": version,
                      "compiler_sha256": digest(compiler.read_bytes()),
                      "compiler": str(compiler.relative_to(ROOT)).replace("\\", "/"),
                      "host_pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
                      "not_target_abi_or_asan": True},
        "player_anchor": before,
        "protected_anchors_unchanged_after_audit": True,
        "harness_sources_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path.read_bytes())
                                  for path in sorted(HARNESS.iterdir()) if path.is_file()},
        "scope": {"actual_call": "unmodified eaf_decode_block from hash-anchored gfx_eaf_dec.c",
                  "allocator_formula": "mirrors gfx_anim width*(block_height+block_height%2)/2 for 4-bit",
                  "length_measurement": "additional registered callback invocation; output compared separately",
                  "allocation_shim": "production malloc/calloc/free redirected to guard allocations; function bodies unchanged",
                  "output_guard": "logical allocation inside oversized red zones; not proof against arbitrary malicious code",
                  "excluded": ["JPEG", "heatshrink", "full EAF C parser on malformed files", "full gfx_anim render/clipping",
                               "LVGL/timers/threads", "LCD", "target ABI/unaligned accesses", "general fuzzing"]},
        **native,
        "next_gate": "do not deploy; separate bounded decoder contract from behavior-binding audit and layout/clip validation",
    }
    immutable(output / "player-bounds-audit-v1.json", (json.dumps(results, indent=2) + "\n").encode())
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--compiler", type=Path, default=COMPILER)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dll", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if not args.dll or not args.result:
            parser.error("worker requires DLL and result")
        if args.dll.resolve().parent != safe_output(args.result.parent):
            parser.error("worker inputs must share the isolated output stage")
        worker(args.dll, args.result)
    else:
        result = build_and_audit(args.output, args.compiler)
        print(json.dumps({"result": result["result"], "device_package_eligible": False,
                          "animations": {key: {k: v for k, v in value.items() if k != "decoded_frame_sha256"}
                                         for key, value in result["animations"].items()},
                          "adversarial_cases": len(result["controlled_adversarial_cases"])}, indent=2))


if __name__ == "__main__":
    main()
