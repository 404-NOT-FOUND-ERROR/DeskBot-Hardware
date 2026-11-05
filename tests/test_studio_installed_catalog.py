"""Read-only directory transfer: correlation, bounds, mixed versions and data integrity."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import jsonschema
from protocol import studio_installed_catalog as c

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = dict(target_id="device-" + "a" * 32, boot_id="boot-" + "b" * 32,
               session_id="session-" + "b" * 32 + "-1")


def sample():
    eye = dict(kind="expression", id="neutral", resource_id="eye.eaf", resource_sha256="a" * 64,
        bytes=1000, layout_id="eye_anim", fps=20, loop=False, implicit_action_id=None)
    return c.build_manifest([
        eye,
        dict(eye, id="happy", loop=True, implicit_action_id="shake"),
        dict(eye, id="listen", kind="status", layout_id="listen_anim", fps=15, loop=True),
        dict(kind="action", id="shake", wire_command=2, wire_value=0, completion="fact_only"),
        dict(kind="layout", id="eye_anim", align="GFX_ALIGN_LEFT_MID", x=10, y=10, mirror="auto"),
        dict(kind="layout", id="listen_anim", align="GFX_ALIGN_TOP_MID", x=0, y=25, mirror="false"),
    ], "c" * 64, "d" * 64)


def pages(manifest=None):
    manifest = manifest or sample()
    return [c.make_page(manifest, **CONTEXT, request_id=f"query-{cursor}", cursor=cursor,
        sequence=cursor + 1, at_ms=cursor * 1000) for cursor in range(0, len(manifest["entries"]), c.PAGE_SIZE)]


class CatalogTests(unittest.TestCase):
    def test_schema_matches_valid_manifest_and_pages(self):
        schema = json.loads((ROOT / "protocol/deskbot-studio-catalog-page-v1.schema.json").read_bytes())
        jsonschema.Draft202012Validator.check_schema(schema)
        for row in [sample(), *pages()]:
            jsonschema.validate(row, schema)

    def test_complete_roundtrip_only_confirms_last_page(self):
        collector = c.CatalogCollector(**CONTEXT)
        rows = pages()
        for row in rows[:-1]:
            self.assertIsNone(collector.accept(row, request_id=row["request_id"], cursor=row["cursor"]))
            self.assertIsNone(collector.confirmed)
        self.assertEqual(collector.accept(rows[-1], request_id=rows[-1]["request_id"], cursor=rows[-1]["cursor"]), sample())

    def test_query_reuses_session_envelope(self):
        raw = c.encode_query(**CONTEXT, client_nonce="client-test", request_id="query-2", cursor=2)
        from protocol.studio_usb_session import decode_request
        self.assertEqual(decode_request(raw)["operation"], "catalog.2")
        self.assertLess(len(raw), 2048)

    def test_query_requires_session_and_even_cursor(self):
        for cursor in (True, -2, 1, 128, 2.0):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                c.encode_query(**CONTEXT, client_nonce="client-test", request_id="query", cursor=cursor)
        with self.assertRaises(ValueError):
            c.encode_query(**(CONTEXT | dict(session_id=None)), client_nonce="client-test", request_id="query")

    def test_canonical_decode_and_duplicate_key_rejection(self):
        page = pages()[0]
        self.assertEqual(c.decode_page(c.canonical(page)), page)
        with self.assertRaises(ValueError):
            c.decode_page(c.canonical(page)[:-1] + b',"cursor":0}')

    def test_oversize_and_non_ascii_input(self):
        for raw in (b"", b" " * 2049, "你好".encode(), b"["):
            with self.subTest(raw=raw[:10]), self.assertRaises(ValueError):
                c.decode_page(raw)

    def test_manifest_digest_detects_data_change(self):
        row = sample()
        row["entries"][0]["fps"] = 21
        with self.assertRaisesRegex(ValueError, "digest"):
            c.validate_manifest(row)

    def test_unknown_extra_fields_rejected(self):
        for row, validate in ((sample(), c.validate_manifest), (pages()[0], c.validate_page)):
            row["play"] = True
            with self.assertRaises(ValueError):
                validate(row)

    def test_entry_type_checks_and_path_bounds(self):
        row = sample()["entries"][0]
        for changes in (dict(bytes=True), dict(fps=1.0), dict(loop=1), dict(id="x\n"),
                dict(resource_id="../eye.eaf"), dict(resource_id="x" * 28 + ".eaf"),
                dict(resource_sha256="A" * 64), dict(kind="new"), dict(layout_id="full_face")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.validate_entry(row | changes)

    def test_duplicate_semantic_rejected_even_with_updated_digest(self):
        m = sample()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            c.build_manifest(m["entries"] + [m["entries"][0]], m["package_sha256"], m["index_sha256"])

    def test_cross_kind_action_name_is_allowed(self):
        m = sample()
        m["entries"][1]["id"] = "shake"
        c.build_manifest(m["entries"], m["package_sha256"], m["index_sha256"])

    def test_shared_file_has_one_content_fingerprint(self):
        m = sample()
        m["entries"][1]["resource_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "shared_resource"):
            c.build_manifest(m["entries"], m["package_sha256"], m["index_sha256"])

    def test_missing_layout_or_action_rejected(self):
        for kind in ("layout", "action"):
            m = sample()
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "reference"):
                c.build_manifest([r for r in m["entries"] if r["kind"] != kind], m["package_sha256"], m["index_sha256"])

    def test_listen_is_owned_by_voice_state(self):
        row = sample()["entries"][2]
        for changes in (dict(kind="expression"), dict(implicit_action_id="shake"), dict(layout_id="eye_anim")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.validate_entry(row | changes)

    def test_duplicate_action_wire_is_rejected(self):
        m = sample()
        extra = dict(m["entries"][3], id="other")
        with self.assertRaisesRegex(ValueError, "wire"):
            c.build_manifest(m["entries"] + [extra], m["package_sha256"], m["index_sha256"])

    def test_unknown_action_values_and_completion_claims(self):
        row = sample()["entries"][3]
        for changes in (dict(wire_value=5), dict(wire_value=True), dict(wire_command=3), dict(completion="correlated")):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.validate_entry(row | changes)

    def test_page_geometry_and_controls(self):
        for changes in (dict(cursor=True), dict(cursor=1), dict(total=129), dict(next_cursor=None),
                dict(next_cursor=True), dict(entries=[]), dict(controls_available=True), dict(sequence=0)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.validate_page(pages()[0] | changes)

    def test_page_boot_and_session_correspond(self):
        with self.assertRaises(ValueError):
            c.validate_page(pages()[0] | dict(boot_id="boot-" + "c" * 32))

    def test_missing_first_page_never_becomes_installed(self):
        collector = c.CatalogCollector(**CONTEXT)
        row = pages()[1]
        with self.assertRaises(ValueError):
            collector.accept(row, request_id=row["request_id"], cursor=row["cursor"])
        self.assertIsNone(collector.confirmed)
        self.assertFalse(collector.active)

    def test_wrong_request_clears_partial_directory(self):
        collector = c.CatalogCollector(**CONTEXT)
        rows = pages()
        collector.accept(rows[0], request_id=rows[0]["request_id"], cursor=0)
        with self.assertRaises(ValueError):
            collector.accept(rows[1], request_id="different", cursor=2)
        self.assertEqual(collector.entries, [])

    def test_wrong_target_and_old_boot_or_session_clear_directory(self):
        for field, value in (("target_id", "device-" + "c" * 32), ("boot_id", "boot-" + "c" * 32),
                             ("session_id", "session-" + "b" * 32 + "-2")):
            collector = c.CatalogCollector(**CONTEXT)
            row = pages()[0] | {field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                collector.accept(row, request_id=row["request_id"], cursor=0)
            self.assertFalse(collector.active)

    def test_mixed_versions_never_publish_partial_manifest(self):
        for field in ("catalog_id", "package_sha256", "index_sha256", "total"):
            collector = c.CatalogCollector(**CONTEXT)
            first, second = pages()[:2]
            collector.accept(first, request_id=first["request_id"], cursor=0)
            second[field] = 8 if field == "total" else "f" * 64
            with self.subTest(field=field), self.assertRaises(ValueError):
                collector.accept(second, request_id=second["request_id"], cursor=2)
            self.assertIsNone(collector.confirmed)

    def test_replay_and_backwards_device_time(self):
        for changes in (dict(sequence=1), dict(at_ms=-1)):
            collector = c.CatalogCollector(**CONTEXT)
            first, second = pages()[:2]
            collector.accept(first, request_id=first["request_id"], cursor=0)
            second.update(changes)
            with self.assertRaises(ValueError):
                collector.accept(second, request_id=second["request_id"], cursor=2)

    def test_final_digest_checked_after_page_content_tamper(self):
        collector = c.CatalogCollector(**CONTEXT)
        rows = pages()
        rows[0]["entries"][0]["fps"] += 1
        with self.assertRaisesRegex(ValueError, "digest"):
            for row in rows:
                collector.accept(row, request_id=row["request_id"], cursor=row["cursor"])
        self.assertIsNone(collector.confirmed)

    def test_disconnect_removes_confirmed_directory(self):
        collector = c.CatalogCollector(**CONTEXT)
        for row in pages():
            collector.accept(row, request_id=row["request_id"], cursor=row["cursor"])
        self.assertIsNotNone(collector.confirmed)
        collector.disconnect()
        self.assertIsNone(collector.confirmed)
        self.assertEqual(collector.entries, [])

    def test_128_entry_bound(self):
        m = sample()
        extras = [dict(m["entries"][0], id="item" + str(n)) for n in range(122)]
        c.build_manifest(m["entries"] + extras, m["package_sha256"], m["index_sha256"])
        with self.assertRaises(ValueError):
            c.build_manifest(m["entries"] + extras + [dict(extras[0], id="overflow")], m["package_sha256"], m["index_sha256"])


if __name__ == "__main__":
    unittest.main()
