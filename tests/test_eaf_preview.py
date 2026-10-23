from __future__ import annotations

import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from protocol import eaf_preview as preview


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "firmware/body"
ASSET_DIR = SOURCE / "main/boards/esp_vocat/assets/emoji_large"
CAPS = ROOT / "tmp/expression-e1-20261019/capabilities.json"


class EafPreviewTests(unittest.TestCase):
    def test_real_official_assets_decode_and_metadata_is_not_a_frame(self) -> None:
        if not CAPS.is_file():
            self.skipTest("the local official-source capability report is not present")
        data = json.loads(CAPS.read_text(encoding="utf-8"))
        paths = {ASSET_DIR / item["src"] for item in data["expressions"]}
        paths.add(ASSET_DIR / data["listening"]["src"])
        decoded = {path.name: preview.decode_eaf(path) for path in paths}
        self.assertEqual(len(decoded), 18)
        self.assertEqual(len(decoded["Happy.eaf"].frames), 42)
        self.assertEqual(decoded["look_around.eaf"].width, 360)
        self.assertEqual(len(decoded["look_around.eaf"].frames), 125)
        self.assertEqual(decoded["listen.eaf"].metadata_frame_count, 1)
        self.assertEqual(decoded["listen.eaf"].stored_frame_count,
                         len(decoded["listen.eaf"].frames) + 1)

    def test_bad_checksum_and_truncated_rle_are_rejected(self) -> None:
        source = ASSET_DIR / "Happy.eaf"
        if not source.is_file():
            self.skipTest("the local official source is not present")
        raw = bytearray(source.read_bytes())
        raw[-1] ^= 0x01
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.eaf"
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, "checksum"):
                preview.decode_eaf(path)
        with self.assertRaisesRegex(ValueError, "incomplete pair"):
            preview._rle_decode(b"\x01", 1, "test")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            preview._rle_decode(b"\x02\x07", 1, "test")

    def test_circle_eye_layout_mirrors_with_device_margin(self) -> None:
        width, height = 125, 160
        pixel = bytes((240, 120, 30, 255))
        frame = pixel * (width * height)
        composed = preview.compose_display_frame(frame, width, height, object_kind="eye")
        self.assertEqual(len(composed), 360 * 360 * 4)
        row = (110 * 360) * 4
        alpha_x = [x for x in range(360) if composed[row + x * 4 + 3]]
        self.assertEqual((min(alpha_x), max(alpha_x)), (10, 349))
        self.assertEqual(composed[row + 10 * 4:row + 10 * 4 + 4], pixel)
        self.assertEqual(composed[row + 349 * 4:row + 349 * 4 + 4], pixel)

    def test_large_eye_is_clipped_not_rejected(self) -> None:
        pixel = bytes((10, 20, 30, 255))
        composed = preview.compose_display_frame(pixel * (360 * 360), 360, 360, object_kind="eye")
        active = [x for x in range(360) if composed[(10 * 360 + x) * 4 + 3]]
        self.assertEqual((min(active), max(active)), (10, 349))
        self.assertEqual(composed[(10 * 360 + 10) * 4:(10 * 360 + 10) * 4 + 4], pixel)

    def test_dialog_and_listen_positions(self) -> None:
        pixel = bytes((1, 2, 3, 255))
        frame = pixel * 4
        dialog = preview.compose_display_frame(frame, 2, 2, object_kind="dialog")
        listen = preview.compose_display_frame(frame, 2, 2, object_kind="listen")
        self.assertEqual(dialog[(179 * 360 + 179) * 4:(179 * 360 + 179) * 4 + 4], pixel)
        self.assertEqual(listen[(25 * 360 + 179) * 4:(25 * 360 + 179) * 4 + 4], pixel)

    def test_png_atlas_header_and_dimensions(self) -> None:
        frames = (bytes((255, 0, 0, 255)) * 4, bytes((0, 255, 0, 255)) * 4)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "atlas.png"
            result = preview.write_rgba_atlas(path, frames, 2, 2, columns=2)
            raw = path.read_bytes()
        self.assertTrue(raw.startswith(preview.PNG_SIGNATURE))
        self.assertEqual(struct.unpack_from(">II", raw, 16), (4, 2))
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(zlib.crc32(b"IHDR" + raw[16:29]) & 0xFFFFFFFF,
                         struct.unpack_from(">I", raw, 29)[0])


if __name__ == "__main__":
    unittest.main()
