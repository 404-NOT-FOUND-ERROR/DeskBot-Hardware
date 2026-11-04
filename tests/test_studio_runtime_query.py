"""Production USB endpoint -> state JSON -> bounded PC state client."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

import jsonschema
from protocol import studio_runtime_query as query
from protocol import studio_usb_session as session

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware/body/main"
BOARD = MAIN / "boards/esp_vocat"
TARGET, BOOT = "device-" + "a"*32, "boot-" + "b"*32
FIRST = "session-" + "b"*32 + "-1"
SETTLED = ("snapshot", 1, 2, 3, 1, 2, 1)


class RuntimeQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("query_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        found = helper.find_compiler()
        if found is None:
            raise RuntimeError("C++17 compiler required")
        command, kind = found
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c3-query-test-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        cls.exe = cls.folder / "query.exe"
        result = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
            "-I", str(MAIN), "-I", str(BOARD), str(ROOT / "tests/cpp/studio_runtime_query_test.cc"),
            "-o", str(cls.exe)], cwd=ROOT, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        cls.schema = json.loads((ROOT / "protocol/deskbot-studio-state-v1.schema.json").read_bytes())
        jsonschema.Draft202012Validator.check_schema(cls.schema)
        cls.validator = jsonschema.Draft202012Validator(cls.schema)

    def wire(self, operation="state", **changes):
        return session.encode_request(**(dict(operation=operation, request_id="r1", target_id=TARGET,
                                             boot_id=BOOT, client_nonce="client-a", session_id=FIRST) | changes))

    def open(self, **changes):
        return self.wire("open", **(dict(session_id=None) | changes))

    def trace(self, *operations):
        raw = bytearray()
        for operation in operations:
            if operation is None:
                raw.extend(b"X")
            elif operation == "reboot":
                raw.extend(b"B")
            elif isinstance(operation, int):
                raw.extend(b"T" + struct.pack("<Q", operation))
            elif operation[0] == "snapshot":
                raw.extend(b"S" + struct.pack("<IBBIBB", *operation[1:]))
            elif operation[0] == "provider":
                raw.extend(b"P" + bytes([operation[1]]))
            elif operation[0] == "capacity":
                raw.extend(b"C" + struct.pack("<I", operation[1]))
            else:
                now, data = operation
                raw.extend(b"D" + struct.pack("<QI", now, len(data)) + data)
        path = self.folder / "trace.bin"
        path.write_bytes(raw)
        result = subprocess.run([str(self.exe), str(path)], capture_output=True, text=True, timeout=10, check=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        for row in rows:
            if row["schema"] == query.SCHEMA:
                query.validate_reply(row)
                self.validator.validate(row)
            else:
                session.validate_reply(row)
        return rows, int(result.stderr)

    def sample(self):
        return self.trace((0, self.open()), SETTLED, (500, self.wire()))[0][-1]

    def test_initial_unknown_is_returned_after_session_open(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire()))
        self.assertEqual(reads, 1)
        self.assertEqual(rows[-1]["application"], dict(revision=0, phase="unknown", value=None))
        self.assertEqual(rows[-1]["sleep"], "unknown")
        self.assertFalse(rows[-1]["controls_available"])

    def test_all_application_values(self):
        for code, name in enumerate(query.APPLICATIONS, 1):
            with self.subTest(name=name):
                rows, reads = self.trace((0, self.open()), ("snapshot", 1, 2, code, 1, 2, 1), (500, self.wire()))
                self.assertEqual(rows[-1]["application"]["value"], name)
                self.assertEqual(reads, 1)

    def test_page_values(self):
        for code, value in ((1, "home"), (2, "other")):
            rows, _ = self.trace((0, self.open()), ("snapshot", 1, 2, 3, 4, 2, code), (500, self.wire()))
            self.assertEqual(rows[-1]["page"], dict(revision=4, phase="settled", value=value))

    def test_transition_does_not_expose_previous_value(self):
        rows, _ = self.trace((0, self.open()), ("snapshot", 2, 1, 6, 3, 1, 1), (500, self.wire()))
        for name in ("application", "page"):
            self.assertEqual(rows[-1][name]["phase"], "transitioning")
            self.assertIsNone(rows[-1][name]["value"])

    def test_unknown_and_settled_channels_are_independent(self):
        rows, _ = self.trace((0, self.open()), ("snapshot", 7, 0, 3, 4, 2, 2), (500, self.wire()))
        self.assertIsNone(rows[-1]["application"]["value"])
        self.assertEqual(rows[-1]["page"]["value"], "other")

    def test_invalid_internal_values_normalize_to_unknown(self):
        for phase, value in ((255, 3), (2, 255)):
            rows, _ = self.trace((0, self.open()), ("snapshot", 9, phase, value, 9, phase, value), (500, self.wire()))
            for name in ("application", "page"):
                self.assertEqual(rows[-1][name], dict(revision=9, phase="unknown", value=None))

    def test_zero_revision_cannot_be_settled_or_transitioning(self):
        rows, _ = self.trace((0, self.open()), ("snapshot", 0, 2, 3, 0, 1, 1), (500, self.wire()))
        self.assertEqual(rows[-1]["application"]["phase"], "unknown")
        self.assertEqual(rows[-1]["page"]["phase"], "unknown")

    def test_full_revision_and_64_bit_uptime(self):
        now = 2**32+500
        rows, _ = self.trace((now, self.open()), ("snapshot", 0xffffffff, 2, 3, 0xffffffff, 2, 1), (now+500, self.wire()))
        self.assertEqual(rows[-1]["at_ms"], now+500)
        self.assertEqual(rows[-1]["page"]["revision"], 0xffffffff)

    def test_max_request_identifier_fits_existing_reply_buffer(self):
        rows, _ = self.trace((0, self.open()), SETTLED, (500, self.wire(request_id="r"*96)))
        self.assertEqual(rows[-1]["request_id"], "r"*96)
        self.assertLess(len(json.dumps(rows[-1], separators=(",", ":")).encode()), 1024)

    def test_query_requires_established_session(self):
        rows, reads = self.trace((0, self.wire()))
        self.assertEqual(rows[-1]["state"], "rejected")
        self.assertEqual(reads, 0)

    def test_wrong_identity_nonce_session_never_reads_snapshot(self):
        for change in (dict(target_id="device-other"), dict(boot_id="boot-other"),
                       dict(client_nonce="other"), dict(session_id="session-other"), dict(session_id=None)):
            with self.subTest(change=change):
                rows, reads = self.trace((0, self.open()), (500, self.wire(**change)))
                self.assertEqual(rows[-1]["state"], "rejected")
                self.assertEqual(reads, 0)

    def test_lease_expiration_blocks_query_at_boundary(self):
        rows, reads = self.trace((0, self.open()), (30000, self.wire()))
        self.assertEqual(rows[-1]["reason"], "lease_expired")
        self.assertEqual(reads, 0)

    def test_query_refreshes_lease(self):
        rows, reads = self.trace((0, self.open()), (29999, self.wire()), (59998, self.wire()))
        self.assertEqual(reads, 2)
        self.assertEqual(rows[-1]["schema"], query.SCHEMA)

    def test_rejected_query_does_not_refresh_lease(self):
        rows, reads = self.trace((0, self.open()), (29999, self.wire(client_nonce="other")), (30250, self.wire()))
        self.assertEqual(rows[-1]["reason"], "lease_expired")
        self.assertEqual(reads, 0)

    def test_close_blocks_snapshot_reads(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire("close")), (1000, self.wire()))
        self.assertEqual(rows[-1]["reason"], "session_closed")
        self.assertEqual(reads, 0)

    def test_disconnect_clears_partial_and_new_session_reconnects(self):
        rows, reads = self.trace((0, self.open()), (500, b'{"schema":'), None,
                                  (501, self.wire()), (1000, self.open(client_nonce="client-b")),
                                  (1500, self.wire(client_nonce="client-b", session_id=FIRST[:-1]+"2")))
        self.assertEqual(rows[-1]["schema"], query.SCHEMA)
        self.assertEqual(reads, 1)

    def test_shared_throttle_limits_provider_reads(self):
        rows, reads = self.trace((0, self.open()), (249, self.wire()), (250, self.wire()), (251, self.wire()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(reads, 1)

    def test_fragmented_and_concatenated_queries(self):
        raw = self.wire()
        rows, reads = self.trace((0, self.open()), (500, raw[:30]), (510, raw[30:]+raw))
        self.assertEqual(reads, 1)
        self.assertEqual(rows[-1]["at_ms"], 510)

    def test_partial_timeout_and_oversize_recover(self):
        rows, reads = self.trace((0, self.open()), (500, b'{"schema":'), 2500,
                                  (3000, b"x"*3000+b"\n"+self.wire()))
        self.assertEqual(reads, 1)
        self.assertEqual(rows[-1]["schema"], query.SCHEMA)

    def test_malformed_request_never_reads(self):
        raw = self.wire()[:-2]+b',"operation":"state"}\n'
        rows, reads = self.trace((0, self.open()), (500, raw))
        self.assertEqual(len(rows), 1)
        self.assertEqual(reads, 0)

    def test_expression_request_remains_unsupported(self):
        rows, reads = self.trace((0, self.open()), (500, self.wire("play_expression")))
        self.assertEqual(rows[-1]["reason"], "unsupported_operation")
        self.assertEqual(reads, 0)

    def test_missing_provider_rejects_without_renewing_lease(self):
        rows, reads = self.trace((0, self.open()), ("provider", False),
                                  (29999, self.wire()), (30250, self.wire()))
        self.assertEqual(rows[1]["reason"], "state_unavailable")
        self.assertEqual(rows[-1]["reason"], "lease_expired")
        self.assertEqual(reads, 0)

    def test_short_reply_capacity_does_not_read_or_advance_sequence(self):
        rows, reads = self.trace((0, self.open()), ("capacity", 1023), (500, self.wire()),
                                  ("capacity", 1024), (1000, self.wire()))
        self.assertEqual(rows[-1]["sequence"], 2)
        self.assertEqual(reads, 1)

    def test_reboot_rejects_old_boot_and_session(self):
        new_boot = "boot-"+"c"*32
        rows, reads = self.trace((0, self.open()), "reboot", (500, self.wire()),
                                  (1000, self.open(boot_id=new_boot)),
                                  (1500, self.wire(boot_id=new_boot, session_id="session-"+"c"*32+"-1")))
        self.assertEqual(rows[1]["reason"], "boot_changed")
        self.assertEqual(rows[-1]["boot_id"], new_boot)
        self.assertEqual(reads, 1)

    def test_validators_reject_inconsistent_channel(self):
        sample = self.sample()
        for change in (dict(revision=True), dict(revision=-1), dict(revision=0), dict(phase="other"),
                       dict(phase="unknown"), dict(phase="transitioning"), dict(value=None), dict(value="happy")):
            value = deepcopy(sample)
            value["application"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                query.validate_reply(value)
            self.assertFalse(self.validator.is_valid(value))

    def test_validators_reject_capabilities_sleep_and_extra_fields(self):
        sample = self.sample()
        for change in (dict(controls_available=True), dict(sleep="awake"), dict(sequence=True),
                       dict(extra=0), dict(lease_ms=0)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                query.validate_reply(sample | change)
            self.assertFalse(self.validator.is_valid(sample | change))
        with self.assertRaises(ValueError):
            query.validate_reply(sample | dict(session_id="session-"+"c"*32+"-1"))

    def test_reply_decoder_rejects_duplicate_keys_and_oversize(self):
        sample = json.dumps(self.sample(), separators=(",", ":")).encode()
        self.assertEqual(query.decode_reply(sample)["page"]["value"], "home")
        for raw in (sample[:-1]+b',"sleep":"unknown"}', b"x"*1025, sample+b"{}", b"\xff"):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                query.decode_reply(raw)

    def client(self):
        client = query.RuntimeQueryClient(TARGET, BOOT, "client-a")
        wire = client.request("open", 0)
        trace = [(0, wire), SETTLED]
        row = self.trace(*trace)[0][-1]
        client.accept(row, 10)
        return client, trace

    def client_state(self):
        client, trace = self.client()
        trace.append((500, client.request("state", 500)))
        row = self.trace(*trace)[0][-1]
        client.accept(row, 510)
        return client, trace, row

    def test_pc_native_roundtrip_and_conservative_age(self):
        client, _, _ = self.client_state()
        state = client.snapshot(600)
        self.assertEqual(state["state"]["application"]["value"], "idle")
        self.assertEqual(state["age_ms"], 100)

    def test_pc_cache_expires_at_exact_boundary(self):
        client, _, _ = self.client_state()
        self.assertIsNotNone(client.snapshot(2499)["state"])
        self.assertIsNone(client.snapshot(2500)["state"])

    def test_pc_session_expires_from_request_send_time(self):
        client, _, _ = self.client_state()
        self.assertEqual(client.snapshot(30500)["reason"], "session_required")
        with self.assertRaises(ValueError): client.request("state", 30500)
        client, trace = self.client()
        trace.append((29999, client.request("state", 29999)))
        client.accept(self.trace(*trace)[0][-1], 30010)
        self.assertEqual(client.snapshot(30010)["reason"], "current")
        self.assertEqual(client.snapshot(59999)["reason"], "session_required")

    def test_pc_disconnect_clears_everything(self):
        client, _, _ = self.client_state()
        client.disconnect()
        self.assertEqual(client.snapshot(600)["reason"], "disconnected")
        with self.assertRaises(ValueError): client.request("open", 700)

    def test_pc_reconnect_rotates_request_ids_and_rejects_old_reply(self):
        client, trace, old = self.client_state()
        client.disconnect()
        client.reconnect("client-b")
        trace.extend([None, (1000, client.request("open", 1000))])
        with self.assertRaises(ValueError): client.accept(old, 1001)
        client.accept(self.trace(*trace)[0][-1], 1010)
        self.assertTrue(client.session_id.endswith("-2"))
        self.assertIsNone(client.snapshot(1010)["state"])

    def test_pc_reconnect_requires_new_nonce(self):
        client, _ = self.client()
        with self.assertRaises(ValueError): client.reconnect("client-a")
        with self.assertRaises(ValueError): client.reconnect("client-b", boot_id="")
        self.assertEqual(client.nonce, "client-a")
        self.assertEqual(client.boot_id, BOOT)

    def test_pc_bounded_pending_timeout_and_late_response(self):
        client, trace = self.client()
        trace.append((500, client.request("state", 500)))
        late = self.trace(*trace)[0][-1]
        with self.assertRaises(ValueError): client.request("state", 501)
        client.request("state", 2500)
        with self.assertRaises(ValueError): client.accept(late, 2510)
        self.assertIsNone(client.snapshot(2510)["state"])

    def test_pc_replay_does_not_refresh_state(self):
        client, _, row = self.client_state()
        with self.assertRaises(ValueError): client.accept(row, 1000)
        self.assertIsNone(client.snapshot(2500)["state"])

    def test_pc_pending_rejects_replay_and_wrong_session_then_accepts_current(self):
        client, trace, old = self.client_state()
        trace.append((1000, client.request("state", 1000)))
        row = self.trace(*trace)[0][-1]
        for changes in (dict(sequence=old["sequence"]), dict(session_id=FIRST[:-1]+"2"), dict(target_id="device-"+"d"*32)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                client.accept(row | changes, 1001)
        client.accept(row, 1010)
        self.assertEqual(client.snapshot(1010)["age_ms"], 10)

    def test_pc_unknown_state_replaces_previous_known_state(self):
        client, trace, _ = self.client_state()
        trace.extend([("snapshot", 2, 1, 3, 2, 0, 1), (1000, client.request("state", 1000))])
        client.accept(self.trace(*trace)[0][-1], 1010)
        row = client.snapshot(1010)["state"]
        self.assertEqual(row["application"]["phase"], "transitioning")
        self.assertIsNone(row["application"]["value"])
        self.assertIsNone(row["page"]["value"])

    def test_pc_device_clock_reversal_clears_state(self):
        client, trace, _ = self.client_state()
        trace.append((1000, client.request("state", 1000)))
        row = self.trace(*trace)[0][-1] | dict(at_ms=499)
        with self.assertRaises(ValueError): client.accept(row, 1010)
        self.assertEqual(client.snapshot(1010)["reason"], "disconnected")

    def test_pc_rejects_non_object_reply(self):
        client, _ = self.client()
        client.request("state", 500)
        for value in (None, [], "state"):
            with self.subTest(value=value), self.assertRaises(ValueError): client.accept(value, 510)

    def test_pc_rejected_response_clears_cached_state(self):
        client, trace, _ = self.client_state()
        wire = client.request("state", 1000)
        value = json.loads(wire)
        value["session_id"] = "session-other"
        trace.append((1000, json.dumps(value).encode()+b"\n"))
        client.accept(self.trace(*trace)[0][-1], 1010)
        self.assertEqual(client.snapshot(1010)["reason"], "session_required")

    def test_pc_changed_boot_clears_state(self):
        client, _, row = self.client_state()
        wire = client.request("state", 1000)
        value = row | dict(request_id=json.loads(wire)["request_id"], sequence=3,
                           boot_id="boot-"+"c"*32, session_id="session-"+"c"*32+"-1")
        with self.assertRaises(ValueError): client.accept(value, 1010)
        self.assertEqual(client.snapshot(1010)["reason"], "disconnected")

    def test_pc_local_clock_reversal_clears_state(self):
        client, _, _ = self.client_state()
        with self.assertRaises(ValueError): client.snapshot(400)
        self.assertEqual(client.snapshot(600)["reason"], "disconnected")

    def test_pc_rejects_bad_local_clock_types(self):
        for value in (True, -1, 1.5, query.MAX_CLOCK+1):
            client, _ = self.client()
            with self.subTest(value=value), self.assertRaises(ValueError): client.snapshot(value)

    def test_pc_rate_limit_and_operation_scope(self):
        client, _ = self.client()
        with self.assertRaises(ValueError): client.request("state", 249)
        with self.assertRaises(ValueError): client.request("play_expression", 250)
        client.request("state", 250)

    def test_source_scope_and_owner_logic_unchanged(self):
        before = json.loads((ROOT / "artifacts/deskbot-studio-e3c2-20261105/firmware-build-report.json").read_bytes())["source_sha256"]
        for name in ("main/application.cc", "main/boards/esp_vocat/ui_bridge.cc",
                     "main/boards/esp_vocat/studio_runtime_state.cc", "main/boards/esp_vocat/studio_runtime_snapshot.h"):
            path = ROOT / "firmware/body" / name
            if name.endswith("studio_runtime_state.cc"):
                text = re.sub(r"#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE\n.*?#endif\n", "",
                              path.read_text(encoding="utf-8"), flags=re.S)
                text = re.sub(r"\n{3,}", "\n\n", text)
                self.assertIn(before[name], {hashlib.sha256(v.encode()).hexdigest()
                              for v in (text, text.replace("\n", "\r\n"))})
            else:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before[name])
        text = (BOARD / "studio_runtime_query.h").read_text()
        for forbidden in ("Application::", "lv_obj", "esp_lv_adapter", "SetEmotion", "xTaskCreate", "malloc(", "new "):
            self.assertNotIn(forbidden, text)

    def test_query_option_is_default_off_and_reuses_task(self):
        stanza = (MAIN / "Kconfig.projbuild").read_text(encoding="utf-8").split("config DESKBOT_STUDIO_STATE_QUERY_ENABLE", 1)[1].split("\nconfig ", 1)[0]
        self.assertIn("depends on DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE", stanza)
        self.assertIn("default n", stanza)
        adapter = (BOARD / "studio_usb_diagnostic.cc").read_text()
        self.assertEqual(adapter.count("xTaskCreate("), 1)
        self.assertIn("endpoint.SetStateProvider(deskbot_studio_runtime::Read)", adapter)


if __name__ == "__main__":
    unittest.main()
