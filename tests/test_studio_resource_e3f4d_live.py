"""Live-evidence correlation uses saved values, without HTTP or device access."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e3f4d_live_evidence", ROOT / "scripts/verify-studio-resources-e3f4d-live.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class LiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.context = ("connection-1", "device-1", "boot-1", "session-1")
        self.report = dict(zip(("connection_id", "target_id", "boot_id", "session_id"), self.context)) | {"catalog_id": "a" * 64}
        self.hello = dict(device_id="device-1", boot_id="boot-1", firmware=dict(app_elf_sha256=live.APP_ELF_SHA))
        self.state = self.report | dict(catalog=dict(status="complete", manifest=dict(catalog_id="a" * 64)))
        self.row = dict(source="device", correlated=True, target_id="device-1", boot_id="boot-1",
                        session_id="session-1", request_id="request-1", request_sequence=1, status="accepted")

    def test_exact_device_catalog_and_session_context(self):
        self.assertEqual(live.checked_context(self.report, self.state, self.hello), self.context)

    def test_reconnect_reboot_and_catalog_change_require_prepare(self):
        for key in ("connection_id", "target_id", "boot_id", "session_id"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                live.checked_context(self.report, self.state | {key: "other"}, self.hello)
        state = deepcopy(self.state)
        state["catalog"]["manifest"]["catalog_id"] = "b" * 64
        with self.assertRaises(ValueError):
            live.checked_context(self.report, state, self.hello)

    def test_changed_app_elf_rejected(self):
        hello = self.hello | {"firmware": dict(app_elf_sha256="b" * 64)}
        with self.assertRaises(ValueError):
            live.checked_context(self.report, self.state, hello)

    def test_only_one_bound_device_request_pair(self):
        rows = [self.row, self.row | {"status": "dispatched"}]
        state = self.state | dict(receipts=rows, last_command=dict(command_id="command-1"))
        self.assertEqual(live.selected_receipts(state, set(), self.context, "command-1"), rows)
        self.assertEqual(live.selected_receipts(state, {"request-1"}, self.context, "command-1"), [])

    def test_unrelated_receipt_or_other_command_rejected(self):
        for key, value in (("source", "host"), ("correlated", False), ("boot_id", "old"),
                           ("session_id", "old"), ("request_id", "second"), ("request_sequence", 2)):
            state = self.state | dict(receipts=[self.row, self.row | {key: value}], last_command=dict(command_id="command-1"))
            with self.subTest(key=key), self.assertRaises(ValueError):
                live.selected_receipts(state, set(), self.context, "command-1")
        state = self.state | dict(receipts=[self.row], last_command=dict(command_id="another-command"))
        with self.assertRaises(ValueError):
            live.selected_receipts(state, set(), self.context, "command-1")

    def test_final_dispatch_without_accepted_is_sufficient_and_explicit(self):
        row = self.row | dict(status="dispatched", reason="submitted_to_display")
        self.assertTrue(live.dispatch_verified([row]))
        self.assertFalse(live.dispatch_verified([]))
        self.assertFalse(live.dispatch_verified([self.row | dict(reason="queued_for_owner")]))

    def test_conflicting_terminal_status_or_reason_rejected(self):
        dispatch = self.row | dict(status="dispatched", reason="submitted_to_display")
        for rows in ([dispatch, dispatch], [dispatch | dict(reason="unknown")],
                     [dispatch, self.row | dict(status="rejected", reason="busy")]):
            with self.assertRaises(ValueError):
                live.dispatch_verified(rows)


if __name__ == "__main__":
    unittest.main()
