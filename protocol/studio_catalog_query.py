"""Catalog page requests carried by the existing read-only USB session."""
from copy import deepcopy
import hashlib

from protocol import studio_installed_catalog as catalog
from protocol import studio_usb_session as session
from protocol.studio_display_query import DisplayQueryClient


class CatalogQueryClient(DisplayQueryClient):
    def request_catalog(self, cursor, now):
        self._tick(now)
        if (type(cursor) is not int or not 0 <= cursor < catalog.MAX_ENTRIES
                or cursor % catalog.PAGE_SIZE or not self.connected or not self.session_id
                or self._pending or (self._last_request is not None
                and now - self._last_request < session.REPLY_INTERVAL_MS)
                or self._counter == 0xffffffff):
            raise ValueError("catalog_request_not_ready")
        self._counter += 1
        token = hashlib.sha256(self.nonce.encode("ascii")).hexdigest()[:24]
        request_id = f"rq-{token}-{self._counter}"
        operation = f"catalog.{cursor}"
        wire = session.encode_request(operation=operation, request_id=request_id,
            target_id=self.target_id, boot_id=self.boot_id, client_nonce=self.nonce,
            session_id=self.session_id)
        self._pending = dict(operation=operation, request_id=request_id, sent=now,
                             session_id=self.session_id)
        self._last_request = now
        return wire

    def accept_catalog(self, value, now):
        self._tick(now)
        if not self.connected or not self._pending:
            raise ValueError("unsolicited_catalog_page")
        row = catalog.validate_page(value)
        pending = self._pending
        if pending["operation"] != f"catalog.{row['cursor']}":
            raise ValueError("catalog_operation_mismatch")
        if row["target_id"] != self.target_id or row["boot_id"] != self.boot_id:
            self.disconnect()
            raise ValueError("catalog_identity_changed")
        if row["session_id"] != self.session_id or row["session_id"] != pending["session_id"]:
            raise ValueError("catalog_session_changed")
        if row["request_id"] != pending["request_id"]:
            raise ValueError("catalog_request_changed")
        if row["sequence"] <= self._sequence:
            raise ValueError("catalog_reply_replayed")
        if self._device_time is not None and row["at_ms"] < self._device_time:
            self.disconnect()
            raise ValueError("catalog_clock_moved_backwards")
        self._sequence, self._device_time = row["sequence"], row["at_ms"]
        self._lease = pending["sent"] + session.LEASE_MS
        self._pending = None
        return deepcopy(row)
