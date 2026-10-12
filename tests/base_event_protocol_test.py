"""Offline tests for the official AA55 base UART framing contract.

The production parser lives in esp_vocat_base_control.  This test exercises
the same byte-stream contract with deterministic vectors so protocol regressions
are caught without a device or a UART port.
"""

from __future__ import annotations

import unittest


def frame(command: int, data: bytes) -> bytes:
    payload = bytes([command]) + data
    checksum = sum(payload) & 0xFF
    return b"\xAA\x55" + len(payload).to_bytes(2, "big") + payload + bytes([checksum])


class StreamParser:
    """Small test oracle for AA55 framing, matching the official component."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.frames: list[tuple[int, bytes]] = []
        self.rejected = 0

    def feed(self, chunk: bytes) -> None:
        self.buffer.extend(chunk)
        while True:
            header = self.buffer.find(b"\xAA\x55")
            if header < 0:
                # Keep a trailing AA: the next chunk may provide the 55.
                if self.buffer[-1:] == b"\xAA":
                    self.buffer[:] = b"\xAA"
                else:
                    self.buffer.clear()
                return
            if header:
                del self.buffer[:header]
            if len(self.buffer) < 4:
                return
            payload_length = int.from_bytes(self.buffer[2:4], "big")
            # FRAME_MAX_SIZE is 256. The length field includes the command,
            # while the complete frame also needs 5 bytes of framing/checksum.
            if payload_length == 0 or payload_length > 250:
                self.rejected += 1
                del self.buffer[:2]
                continue
            total = 2 + 2 + payload_length + 1
            if len(self.buffer) < total:
                return
            packet = bytes(self.buffer[:total])
            del self.buffer[:total]
            payload = packet[4 : 4 + payload_length]
            if packet[-1] != sum(payload) & 0xFF:
                self.rejected += 1
                continue
            self.frames.append((payload[0], payload[1:]))


class BaseEventProtocolTests(unittest.TestCase):
    def test_standard_eight_byte_frame(self) -> None:
        parser = StreamParser()
        parser.feed(frame(0x03, b"\x00\x01"))
        self.assertEqual(parser.frames, [(0x03, b"\x00\x01")])

    def test_split_input(self) -> None:
        packet = frame(0x02, b"\x00\x10")
        parser = StreamParser()
        for byte in packet:
            parser.feed(bytes([byte]))
        self.assertEqual(parser.frames, [(0x02, b"\x00\x10")])

    def test_concatenated_frames(self) -> None:
        parser = StreamParser()
        parser.feed(frame(0x03, b"\x00\x01") + frame(0x03, b"\x00\x02"))
        self.assertEqual(
            parser.frames,
            [(0x03, b"\x00\x01"), (0x03, b"\x00\x02")],
        )

    def test_bad_checksum_is_rejected(self) -> None:
        packet = bytearray(frame(0x03, b"\x00\x07"))
        packet[-1] ^= 0xFF
        parser = StreamParser()
        parser.feed(bytes(packet))
        self.assertEqual(parser.frames, [])
        self.assertEqual(parser.rejected, 1)

    def test_invalid_lengths_are_rejected(self) -> None:
        parser = StreamParser()
        parser.feed(b"\xAA\x55\x00\x00")
        parser.feed(b"\xAA\x55\x00\xFB")
        self.assertEqual(parser.frames, [])
        self.assertEqual(parser.rejected, 2)

    def test_maximum_official_payload_length_is_accepted(self) -> None:
        parser = StreamParser()
        parser.feed(frame(0x7F, bytes(range(249))))
        self.assertEqual(len(parser.frames), 1)
        self.assertEqual(parser.frames[0][0], 0x7F)
        self.assertEqual(len(parser.frames[0][1]), 249)

    def test_all_semantic_value_ranges_have_valid_frames(self) -> None:
        values = list(range(0x0001, 0x0008))
        values += list(range(0x0008, 0x0014))
        values += [0x0081, 0x0082, 0x0083]
        parser = StreamParser()
        parser.feed(b"".join(frame(0x03, value.to_bytes(2, "big")) for value in values))
        self.assertEqual(len(parser.frames), len(values))

    def test_perception_sample_is_transport_valid_but_not_event(self) -> None:
        parser = StreamParser()
        parser.feed(frame(0x06, bytes([1, 0, 190, 0xFD, 0xDC, 0xFC, 0x54])))
        self.assertEqual(parser.frames, [(0x06, bytes([1, 0, 190, 0xFD, 0xDC, 0xFC, 0x54]))])


if __name__ == "__main__":
    unittest.main()
