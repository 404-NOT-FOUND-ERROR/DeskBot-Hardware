"""Read-only installed-directory contract. No serial, player or filesystem I/O."""
from copy import deepcopy
import hashlib
import json
import re

from protocol import studio_usb_session as session

SCHEMA = "deskbot.studio_catalog_page/v1"
MANIFEST_SCHEMA = "deskbot.installed_expression_manifest/v1"
PAGE_SIZE = 2
MAX_ENTRIES = 128
MAX_FRAME_BYTES = 2048
MAX_CLOCK = 9007199254740991
HASH = re.compile(r"[a-f0-9]{64}\Z")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,30}\Z")
FILE = re.compile(r"[A-Za-z0-9_]{1,27}\.eaf\Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _hash(value):
    return isinstance(value, str) and HASH.fullmatch(value) is not None


def _name(value):
    return isinstance(value, str) and NAME.fullmatch(value) is not None


def _int(value, low, high):
    return type(value) is int and low <= value <= high


def validate_entry(row):
    if not isinstance(row, dict):
        raise ValueError("invalid_catalog_entry")
    kind = row.get("kind")
    if kind in ("expression", "status"):
        if (set(row) != {"kind", "id", "resource_id", "resource_sha256", "bytes", "layout_id", "fps", "loop", "implicit_action_id"}
                or not _name(row["id"]) or not isinstance(row["resource_id"], str) or not FILE.fullmatch(row["resource_id"])
                or not _hash(row["resource_sha256"]) or not _int(row["bytes"], 1, 8 * 1024 * 1024)
                or row["layout_id"] not in ("eye_anim", "listen_anim")
                or not _int(row["fps"], 1, 60) or type(row["loop"]) is not bool
                or (row["implicit_action_id"] is not None and not _name(row["implicit_action_id"]))
                or (kind == "status") != (row["id"] == "listen")
                or (kind == "status") != (row["layout_id"] == "listen_anim")
                or (kind == "status" and row["implicit_action_id"] is not None)):
            raise ValueError("invalid_catalog_expression")
    elif kind == "action":
        if (set(row) != {"kind", "id", "wire_command", "wire_value", "completion"}
                or not _name(row["id"]) or type(row["wire_command"]) is not int or row["wire_command"] != 2
                or not _int(row["wire_value"], 0, 4) or row["completion"] != "fact_only"):
            raise ValueError("invalid_catalog_action")
    elif kind == "layout":
        if (set(row) != {"kind", "id", "align", "x", "y", "mirror"}
                or row["id"] not in ("eye_anim", "listen_anim")
                or row["align"] not in ("GFX_ALIGN_LEFT_MID", "GFX_ALIGN_TOP_MID", "GFX_ALIGN_CENTER")
                or not _int(row["x"], -360, 360) or not _int(row["y"], -360, 360)
                or row["mirror"] not in ("auto", "false", "true")):
            raise ValueError("invalid_catalog_layout")
    else:
        raise ValueError("invalid_catalog_kind")
    return deepcopy(row)


def build_manifest(entries, package_sha256, index_sha256):
    value = dict(schema=MANIFEST_SCHEMA, package_sha256=package_sha256, index_sha256=index_sha256,
                 entries=deepcopy(entries))
    value["catalog_id"] = digest(value)
    validate_manifest(value)
    return value


def validate_manifest(value):
    if (not isinstance(value, dict) or set(value) != {"schema", "catalog_id", "package_sha256", "index_sha256", "entries"}
            or value["schema"] != MANIFEST_SCHEMA or any(not _hash(value[k]) for k in ("catalog_id", "package_sha256", "index_sha256"))
            or not isinstance(value["entries"], list) or not 1 <= len(value["entries"]) <= MAX_ENTRIES):
        raise ValueError("invalid_catalog_manifest")
    ids, actions, layouts, resources, wires = set(), set(), set(), {}, set()
    for raw in value["entries"]:
        row = validate_entry(raw)
        # IDs are unique within their kind: look_around is both an emotion and an action.
        key = row["kind"], row["id"]
        if key in ids:
            raise ValueError("duplicate_catalog_entry")
        ids.add(key)
        if row["kind"] == "action":
            wire = row["wire_command"], row["wire_value"]
            if wire in wires:
                raise ValueError("duplicate_action_wire")
            wires.add(wire)
            actions.add(row["id"])
        elif row["kind"] == "layout":
            layouts.add(row["id"])
        else:
            content = row["resource_sha256"], row["bytes"]
            if resources.setdefault(row["resource_id"], content) != content:
                raise ValueError("shared_resource_content_changed")
    for row in value["entries"]:
        if row["kind"] in ("expression", "status"):
            if row["layout_id"] not in layouts or row["implicit_action_id"] is not None and row["implicit_action_id"] not in actions:
                raise ValueError("missing_catalog_reference")
    if digest({k: v for k, v in value.items() if k != "catalog_id"}) != value["catalog_id"]:
        raise ValueError("catalog_digest_changed")
    return deepcopy(value)


def make_page(manifest, *, request_id, target_id, boot_id, session_id, sequence, at_ms, cursor=0):
    """Offline producer fixture; the future device session supplies this context."""
    manifest = validate_manifest(manifest)
    if not _int(cursor, 0, len(manifest["entries"]) - 1) or cursor % PAGE_SIZE:
        raise ValueError("invalid_catalog_cursor")
    end = min(cursor + PAGE_SIZE, len(manifest["entries"]))
    row = dict(schema=SCHEMA, message_type="catalog_page", request_id=request_id,
        target_id=target_id, boot_id=boot_id, session_id=session_id, sequence=sequence, at_ms=at_ms,
        catalog_id=manifest["catalog_id"], package_sha256=manifest["package_sha256"],
        index_sha256=manifest["index_sha256"], cursor=cursor, next_cursor=end if end < len(manifest["entries"]) else None,
        total=len(manifest["entries"]), entries=manifest["entries"][cursor:end], controls_available=False)
    return validate_page(row)


def validate_page(value):
    keys = {"schema", "message_type", "request_id", "target_id", "boot_id", "session_id", "sequence", "at_ms",
            "catalog_id", "package_sha256", "index_sha256", "cursor", "next_cursor", "total", "entries", "controls_available"}
    if (not isinstance(value, dict) or set(value) != keys or value["schema"] != SCHEMA
            or value["message_type"] != "catalog_page" or not session._id(value["request_id"])
            or not isinstance(value["target_id"], str) or not session.DEVICE.fullmatch(value["target_id"])
            or not isinstance(value["boot_id"], str) or not session.BOOT.fullmatch(value["boot_id"])
            or not isinstance(value["session_id"], str) or not session.SESSION.fullmatch(value["session_id"])
            or not value["session_id"].startswith("session-" + value["boot_id"][5:] + "-")
            or not 1 <= int(value["session_id"].rsplit("-", 1)[1]) <= 0xffffffff
            or not _int(value["sequence"], 1, 0xffffffff) or not _int(value["at_ms"], 0, MAX_CLOCK)
            or any(not _hash(value[k]) for k in ("catalog_id", "package_sha256", "index_sha256"))
            or not _int(value["total"], 1, MAX_ENTRIES) or not _int(value["cursor"], 0, value["total"] - 1)
            or value["cursor"] % PAGE_SIZE or value["controls_available"] is not False
            or not isinstance(value["entries"], list)
            or len(value["entries"]) != min(PAGE_SIZE, value["total"] - value["cursor"])):
        raise ValueError("invalid_catalog_page")
    expected = value["cursor"] + len(value["entries"])
    if value["next_cursor"] != (expected if expected < value["total"] else None) or type(value["next_cursor"]) not in (int, type(None)):
        raise ValueError("invalid_catalog_next_cursor")
    for row in value["entries"]:
        validate_entry(row)
    if len(canonical(value)) + 1 > MAX_FRAME_BYTES:
        raise ValueError("catalog_frame_too_large")
    return deepcopy(value)


def decode_page(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_FRAME_BYTES:
        raise ValueError("catalog_frame_size")
    try:
        return validate_page(json.loads(raw.decode("ascii"), object_pairs_hook=session._unique))
    except (UnicodeError, RecursionError) as error:
        raise ValueError("catalog_frame_json") from error


def encode_query(*, request_id, target_id, boot_id, client_nonce, session_id, cursor=0):
    """Use the existing seven-field USB session envelope for the future reader."""
    if not _int(cursor, 0, MAX_ENTRIES - 1) or cursor % PAGE_SIZE or session_id is None:
        raise ValueError("invalid_catalog_query")
    return session.encode_request(operation=f"catalog.{cursor}", request_id=request_id,
        target_id=target_id, boot_id=boot_id, client_nonce=client_nonce, session_id=session_id)


class CatalogCollector:
    """Assemble one connection's directory; only a complete digest becomes usable."""
    def __init__(self, target_id, boot_id, session_id):
        self.identity = target_id, boot_id, session_id
        self.clear()

    def clear(self):
        self.entries, self.header, self.confirmed = [], None, None
        self.sequence, self.at_ms = 0, -1
        self.active = True

    def disconnect(self):
        self.clear()
        self.active = False

    def accept(self, value, *, request_id, cursor):
        try:
            row = validate_page(value)
            if not self.active or self.confirmed is not None:
                raise ValueError("catalog_connection_closed_or_complete")
            if tuple(row[k] for k in ("target_id", "boot_id", "session_id")) != self.identity:
                raise ValueError("catalog_identity_changed")
            if row["request_id"] != request_id or type(cursor) is not int or row["cursor"] != cursor or cursor != len(self.entries):
                raise ValueError("catalog_request_or_cursor_changed")
            if row["sequence"] <= self.sequence or row["at_ms"] < self.at_ms:
                raise ValueError("catalog_reply_replayed")
            header = {k: row[k] for k in ("catalog_id", "package_sha256", "index_sha256", "total")}
            if self.header is not None and self.header != header:
                raise ValueError("catalog_changed_between_pages")
            self.header = header
            self.entries.extend(row["entries"])
            self.sequence, self.at_ms = row["sequence"], row["at_ms"]
            if row["next_cursor"] is None:
                manifest = dict(schema=MANIFEST_SCHEMA, entries=self.entries,
                    **{k: row[k] for k in ("catalog_id", "package_sha256", "index_sha256")})
                self.confirmed = validate_manifest(manifest)
            return deepcopy(self.confirmed)
        except ValueError:
            self.disconnect()
            raise
