"""Deterministic Studio draft -> legacy resource package. No device I/O."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parents[1]
CATALOG_ID = "8183d6adda628e45d68dc81fdb76b083d0cbc382e8a75e856e95e79a72e667d2"
BASE_PACKAGE_SHA = "9d12465fe68ef1cfe11bb9cec609199e345408e0ec23451a97ff2b08ef6ba5c1"
BASE_IMAGE_SHA = "8e9dae01b99060c313a76f2dfc7c2b854b80c4584d76f607a74dd2903d39fce4"
TARGET_ID = "esp-vocat-n32r16-upper-assets-8m-v1"
TABLE_SHA = "245506195778fcca47a93c90b89c5031bdd7c7b727cb9f674febfd67317a0c8d"
MAX_DRAFT_BYTES = 512 * 1024
TARGET_DIR = ROOT / "firmware/releases/esp_vocat_n32r16"
BASE_IMAGE = ROOT / "tmp/expression-e1-45-staging-20261104/rollback-reference-e1-18-assets-8m.bin"
CANDIDATE = ROOT / "tmp/expression-e1-44-adaptation-20261104/eye-terminal.eaf"
RELEASES = ROOT / "tmp/deskbot-studio-e2b-packages-20261104"
ID_RE = re.compile(r"[a-z][a-z0-9-]{0,63}")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def json_bytes(value, indent=2) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=indent, allow_nan=False) + "\n").encode("utf-8")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 字段重复：" + key)
        value[key] = item
    return value


def parse_draft(raw: bytes | str) -> dict:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) > MAX_DRAFT_BYTES:
        raise ValueError("草稿文件应小于 512 KiB")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON 数值无效")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("草稿应为 UTF-8 JSON") from error


def load_catalog() -> dict:
    """The E2a catalog is immutable; media is omitted from the packager API."""
    path = ROOT / "artifacts/deskbot-studio-e2a-20261104/index.html"
    match = re.search(r'<script id="studio-data" type="application/json">(.*?)</script>',
                      path.read_text(encoding="utf-8"), re.S)
    if match is None:
        raise ValueError("E2a 素材目录缺失")
    data = json.loads(match[1])
    if data["catalog_id"] != CATALOG_ID:
        raise ValueError("素材目录版本已变化")
    return {"catalog_id": data["catalog_id"], "semantics": data["semantics"],
            "assets": {name: {key: value for key, value in row.items() if key != "atlas"}
                       for name, row in data["assets"].items()},
            "initial": data["initial"], "sources": data["sources"]}


def exact_keys(value, names) -> bool:
    return isinstance(value, dict) and set(value) == set(names)


def validate_draft(draft: dict, catalog: dict, *, complete=True) -> dict:
    if (not exact_keys(draft, ("schema", "catalog_id", "scope", "revision", "selected_role_id", "roles"))
            or draft["schema"] != "deskbot.studio_draft/v1" or draft["scope"] != "offline_draft"
            or draft["catalog_id"] != catalog["catalog_id"]):
        raise ValueError("草稿版本或素材目录不匹配")
    if type(draft["revision"]) is not int or not 1 <= draft["revision"] <= 9007199254740991:
        raise ValueError("草稿修订号无效")
    roles = draft["roles"]
    if not isinstance(roles, list) or not 1 <= len(roles) <= 16:
        raise ValueError("角色数量应为 1–16")
    ids = set()
    for role in roles:
        if not exact_keys(role, ("id", "name", "voice", "example", "bindings")):
            raise ValueError("角色字段不完整")
        if (not isinstance(role["id"], str) or not ID_RE.fullmatch(role["id"])
                or role["id"] in ids or type(role["example"]) is not bool):
            raise ValueError("角色编号或示例标记无效")
        for key, limit in (("name", 80), ("voice", 96)):
            if not isinstance(role[key], str) or not role[key].strip() or len(role[key]) > limit:
                raise ValueError("角色名称或声音字段无效")
        ids.add(role["id"])
        if not exact_keys(role["bindings"], catalog["semantics"]):
            raise ValueError("表情清单不完整")
        for semantic, resource in role["bindings"].items():
            if resource is not None and (not isinstance(resource, str)
                    or resource not in catalog["semantics"][semantic]["allowed_resources"]):
                raise ValueError("动画与圆屏布局不兼容：" + semantic)
    if not isinstance(draft["selected_role_id"], str) or draft["selected_role_id"] not in ids:
        raise ValueError("选中的角色不存在")
    role = next(role for role in roles if role["id"] == draft["selected_role_id"])
    if complete and any(value is None for value in role["bindings"].values()):
        raise ValueError("请先接好当前角色的全部 29 个表情")
    # listen is a status icon with its own playback path, not an emotion entry.
    if complete and role["bindings"]["listen"] != "listen.eaf":
        raise ValueError("聆听状态应保留 listen.eaf")
    return role


def selected_input(draft, catalog) -> dict:
    role = validate_draft(draft, catalog)
    # Other roles and edit history do not affect this resource package.
    return {"catalog_id": draft["catalog_id"], "target_id": TARGET_ID, "role": copy.deepcopy(role)}


def load_target() -> dict:
    target = json.loads((TARGET_DIR / "target.json").read_bytes())
    expected = {"schema": "deskbot.release_target/v1", "id": TARGET_ID, "chip": "esp32s3",
                "board": "esp_vocat", "flash_bytes": 33554432, "psram_bytes": 16777216,
                "flash_mode": "dout", "flash_memory_interface": "opi",
                "flash_frequency_mhz": 80, "sample_mode": "str",
                "layout": "upper-assets-8m", "partition_table_sha256": TABLE_SHA,
                "assets": {"name": "assets", "offset": 0x1000000, "bytes": 0x800000,
                           "soft_limit_bytes": 7 * 1024 * 1024},
                "package": {"format": "legacy_mmap_v1", "name_slot_bytes": 32},
                "minimum_app_headroom_bytes": 1048576}
    if target != expected:
        raise ValueError("发布目标与 N32R16 现有布局不一致")
    from protocol.storage_n32r16 import proposals, to_csv
    rows = next(row for row in proposals() if row["id"] == target["layout"])["rows"]
    table = to_csv(rows).encode("utf-8")
    if (TARGET_DIR / "partitions.csv").read_bytes() != table:
        raise ValueError("发布分区 CSV 已变化")
    return target


def load_legacy_tools():
    spec = importlib.util.spec_from_file_location("studio_legacy_packer", ROOT / "scripts/audit-expression-package-candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.AUDIT, module


def extract_package(image: bytes, size: int) -> bytes:
    if len(image) != size or len(image) < 12:
        raise ValueError("资源镜像大小与目标分区不符")
    end = 12 + struct.unpack_from("<I", image, 8)[0]
    if not 12 < end <= size or image[end:] != b"\xff" * (size - end):
        raise ValueError("资源镜像的包边界或填充无效")
    return image[:end]


def _check_asset(name, raw, catalog, resources) -> None:
    asset = catalog["assets"][name]
    if len(raw) != asset["bytes"] or sha(raw) != asset["deployed_sha256"]:
        raise ValueError("资源内容与目录不一致：" + name)
    info = resources.parse_eaf_bytes(raw)
    if (info["image_frame_count"] != asset["frame_count"] or info["dimensions"] != [
            {"width": asset["frame_width"], "height": asset["frame_height"]}]):
        raise ValueError("动画尺寸或帧数不匹配：" + name)


def assemble(draft: dict, catalog: dict, target: dict, baseline_image: bytes, extras: dict) -> tuple[dict, bytes, bytes]:
    role = validate_draft(draft, catalog)
    resources, packer = load_legacy_tools()
    size = target["assets"]["bytes"]
    if sha(baseline_image) != BASE_IMAGE_SHA:
        raise ValueError("E1.18 资源镜像摘要不匹配")
    baseline_package = extract_package(baseline_image, size)
    if sha(baseline_package) != BASE_PACKAGE_SHA:
        raise ValueError("E1.18 资源包摘要不匹配")
    before = resources.parse_package_bytes(baseline_package, modern=False)["members"]
    if packer.build_legacy_package(before) != baseline_package:
        raise ValueError("E1.18 资源包往返复核失败")
    old_index = json.loads(before["index.json"])
    index = copy.deepcopy(old_index)
    rows = {row["name"]: row for row in index["emoji_collection"]}
    if set(rows) != set(catalog["semantics"]) - {"listen"} or len(index["emoji_collection"]) != 28:
        raise ValueError("普通表情索引应包含 28 个语义")
    members = dict(before)
    for name, asset in catalog["assets"].items():
        if asset["origin"] == "official":
            _check_asset(name, before[name], catalog, resources)
    changes = []
    for semantic, source in catalog["semantics"].items():
        name = role["bindings"][semantic]
        if semantic == "listen":
            continue
        row = rows[semantic]
        if row["file"] != source["resource_id"] or row["eaf"] != {"loop": source["loop"], "fps": source["fps"]}:
            raise ValueError("设备索引与工作台目录不一致：" + semantic)
        asset = catalog["assets"][name]
        if asset["object_kind"] != "eye" or source["layout_id"] != "eye_anim":
            raise ValueError("眼部表情布局不匹配：" + semantic)
        if name not in members:
            if name not in extras:
                raise ValueError("本机缺少候选动画：" + name)
            _check_asset(name, extras[name], catalog, resources)
            members[name] = extras[name]
        if name != source["resource_id"]:
            previous = copy.deepcopy(row)
            row["file"] = name
            if asset["origin"] == "candidate":
                row["eaf"] = {"loop": asset["loop"], "fps": asset["fps"]}
            changes.append({"semantic": semantic, "before": previous, "after": copy.deepcopy(row)})
    if changes:
        # Exact serialization used by the accepted E1.45 preparation.
        members["index.json"] = json_bytes(index, indent=4)
    package = packer.build_legacy_package(members)
    if len(package) > target["assets"]["soft_limit_bytes"]:
        raise ValueError("资源包超过 7 MiB 发布上限，请调整所选资源")
    image = package + b"\xff" * (size - len(package))
    decoded = resources.parse_package_bytes(extract_package(image, size), modern=False)["members"]
    if decoded != members or packer.build_legacy_package(decoded) != package:
        raise ValueError("新资源包往返复核失败")
    preserved = sorted(name for name in before if name != "index.json")
    if any(before[name] != decoded[name] for name in preserved):
        raise ValueError("已有资源成员发生变化")
    for key in set(old_index) - {"emoji_collection"}:
        if index[key] != old_index[key]:
            raise ValueError("圆屏布局、图标或模型索引发生变化")
    input_value = selected_input(draft, catalog)
    input_sha = sha(canonical(input_value))
    image_sha = sha(image)
    report = {
        "schema": "deskbot.studio_package/v1", "edition": "E2b", "result": "offline_package_verified",
        "catalog_id": catalog["catalog_id"], "input": input_value, "input_sha256": input_sha,
        "package_id": sha(canonical({"input_sha256": input_sha, "image_sha256": image_sha,
                                     "target": target})),
        "target": target, "baseline": {"edition": "E1.18", "package_sha256": BASE_PACKAGE_SHA,
                                        "image_sha256": BASE_IMAGE_SHA},
        "package": {"bytes": len(package), "sha256": sha(package), "member_count": len(members)},
        "image": {"bytes": len(image), "sha256": image_sha},
        "budget": {"partition_bytes": size, "soft_limit_bytes": target["assets"]["soft_limit_bytes"],
                   "package_bytes": len(package), "free_bytes": size - len(package),
                   "soft_limit_free_bytes": target["assets"]["soft_limit_bytes"] - len(package)},
        "changes": changes, "members": {"added": sorted(set(members) - set(before)), "removed": [],
                    "changed": ["index.json"] if changes else [], "preserved": preserved,
                    "all_existing_non_index_byte_exact": True},
        "checks": {"package_round_trip": True, "layout_unchanged": True, "listen_unchanged": True,
                   "icons_fonts_models_unchanged": True, "action_branch_unchanged": True,
                   "within_release_budget": True, "asset_hashes_and_geometry_match": True},
        "player_evidence": {"source": "E1.44" if "shy_candidate.eaf" in members else "accepted_E1.18",
                            "native_tests_rerun": False, "candidate_bytes_match_reviewed_source":
                            "shy_candidate.eaf" in members},
        "hardware_accessed": False, "device_written": False,
        "next_step": "device_mapping_interaction_and_resource_deployment",
    }
    return report, package, image


def persist(report: dict, package: bytes, image: bytes, root=RELEASES) -> Path:
    """Content-addressed output; repeated requests reuse exactly the same files."""
    directory = Path(root) / report["package_id"]
    products = {"report.json": json_bytes(report), "expression-package.bin": package,
                "expression-assets-8m.bin": image,
                "selected-role.json": json_bytes(report["input"]),
                "members.json": json_bytes(report["members"])}
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in products.items():
        path = directory / name
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("已保存的同版本资源包与本次结果不符：" + name)
        else:
            path.write_bytes(raw)
    return directory


def generate(draft: dict, catalog=None, output=RELEASES) -> tuple[dict, Path]:
    catalog = catalog or load_catalog()
    validate_draft(draft, catalog)
    target = load_target()
    extras = {}
    role = validate_draft(draft, catalog)
    if "shy_candidate.eaf" in role["bindings"].values():
        extras["shy_candidate.eaf"] = CANDIDATE.read_bytes()
    report, package, image = assemble(draft, catalog, target, BASE_IMAGE.read_bytes(), extras)
    return report, persist(report, package, image, output)
