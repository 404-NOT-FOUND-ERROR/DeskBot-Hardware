from __future__ import annotations

import unittest

from protocol.base_event_semantics import (
    UNKNOWN_EVENT,
    map_command_data,
    mechanical_status_name,
)


def value_bytes(value: int) -> bytes:
    return value.to_bytes(2, "big")


class O2aSemanticMappingTests(unittest.TestCase):
    def test_connection_action_and_mechanical_statuses(self) -> None:
        self.assertEqual(
            map_command_data(0x00, value_bytes(1)).type,
            "base.link.connected",
        )
        self.assertEqual(
            map_command_data(0x02, value_bytes(0x10)).type,
            "base.action.completed",
        )
        for value, name in ((0, "homing"), (1, "ready"), (2, "failed")):
            event = map_command_data(0x05, value_bytes(value))
            self.assertTrue(event.known)
            self.assertEqual(event.payload["status"], name)

    def test_all_magnetic_switch_values(self) -> None:
        expected = {
            1: "base.magnetic_switch.slide_down",
            2: "base.magnetic_switch.slide_up",
            3: "base.magnetic_switch.removed_from_up",
            4: "base.magnetic_switch.removed_from_down",
            5: "base.magnetic_switch.placed_from_up",
            6: "base.magnetic_switch.placed_from_down",
            7: "base.magnetic_switch.single_click",
        }
        for value, event_type in expected.items():
            event = map_command_data(0x03, value_bytes(value))
            self.assertTrue(event.known)
            self.assertEqual(event.type, event_type)

    def test_accessory_pairing_and_calibration_values(self) -> None:
        for value, accessory in (
            (0x08, "fish"),
            (0x0C, "ice_cream"),
            (0x0E, "donut"),
            (0x10, "iphone_lean_front"),
            (0x12, "iphone_under_base"),
        ):
            attached = map_command_data(0x03, value_bytes(value))
            detached = map_command_data(0x03, value_bytes(value + 1))
            self.assertEqual(attached.type, "base.accessory.attached")
            self.assertEqual(detached.type, "base.accessory.detached")
            self.assertEqual(attached.payload["accessory"], accessory)
            self.assertEqual(detached.payload["accessory"], accessory)

        self.assertEqual(
            map_command_data(0x03, value_bytes(0x0A)).type,
            "base.pairing.entered",
        )
        self.assertEqual(
            map_command_data(0x03, value_bytes(0x0B)).type,
            "base.pairing.cancelled",
        )
        for value, name in ((0x81, "started"), (0x82, "position_recorded"), (0x83, "completed")):
            event = map_command_data(0x03, value_bytes(value))
            self.assertEqual(event.type, f"base.calibration.{name}")

    def test_perception_is_known_telemetry_but_not_an_event(self) -> None:
        event = map_command_data(0x06, bytes((1, 0, 190, 0xFD, 0xDC, 0xFC, 0x54)))
        self.assertTrue(event.known)
        self.assertTrue(event.ignored)
        self.assertEqual(event.type, UNKNOWN_EVENT)
        self.assertEqual(event.payload["telemetry"], "magnetic_sample")

    def test_unknown_commands_values_and_lengths_are_preserved_as_unknown(self) -> None:
        cases = (
            (0x7F, value_bytes(1)),
            (0x03, value_bytes(0x1234)),
            (0x03, b"\x00"),
            (0x03, b"\x00\x01\x02"),
            (0x06, b"\x01\x02"),
            (0x00, None),
        )
        for command, data in cases:
            event = map_command_data(command, data)
            self.assertFalse(event.known)
            self.assertFalse(event.ignored)
            self.assertEqual(event.type, UNKNOWN_EVENT)

    def test_mechanical_status_name_unknown(self) -> None:
        self.assertEqual(mechanical_status_name(0xFFFF), "unknown")


if __name__ == "__main__":
    unittest.main()
