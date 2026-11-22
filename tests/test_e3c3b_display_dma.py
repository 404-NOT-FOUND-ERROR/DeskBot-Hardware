"""Host regression tests over frozen E3c-3a display functions.

The harness extracts selected real C functions from the retained driver,
adapter, and ESP-IDF files, then compiles them against narrow native stubs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DMA = importlib.util.spec_from_file_location("e3c3b_display_dma", ROOT / "scripts/e3c3b_display_dma.py")
dma = importlib.util.module_from_spec(DMA)
assert DMA.loader is not None
DMA.loader.exec_module(dma)

IDF_ROOT = Path("D:/Espressif/frameworks/esp-idf-v5.5.5")
IDF_SPI_MASTER = IDF_ROOT / "components/esp_driver_spi/src/gpspi/spi_master.c"
IDF_SPI_IO = IDF_ROOT / "components/esp_lcd/spi/esp_lcd_panel_io_spi.c"
HARNESS = ROOT / "tests/cpp/dma_display_harness.c"
SPI_MASTER_SHA256 = "6fa047a8d1b5e1985d518ae4933edba6b741eaf46038dcfaa9310a4223ad5b78"
SPI_IO_SHA256 = "d2a1f50433e91818bfcadc91dadf1a4eab3c0646df7f5c7e2017142c3523c1f1"


def source_path(relative: str) -> Path:
    return dma.FROZEN / relative


def extract_function(source: str, function_name: str) -> str:
    """Extract one complete C function while ignoring braces in comments/strings."""
    search_at = 0
    start = open_brace = -1
    while True:
        name_at = source.find(function_name, search_at)
        if name_at < 0:
            break
        open_paren = source.find("(", name_at + len(function_name))
        if open_paren < 0:
            break
        depth_paren = 0
        close_paren = -1
        for index in range(open_paren, len(source)):
            if source[index] == "(":
                depth_paren += 1
            elif source[index] == ")":
                depth_paren -= 1
                if depth_paren == 0:
                    close_paren = index
                    break
        if close_paren < 0:
            break
        body_at = close_paren + 1
        while body_at < len(source) and source[body_at].isspace():
            body_at += 1
        if body_at < len(source) and source[body_at] == "{":
            start = source.rfind("\n", 0, name_at) + 1
            open_brace = body_at
            break
        search_at = close_paren + 1
    if start < 0:
        raise AssertionError(f"function definition not found: {function_name}")
    depth = 0
    state = "code"
    escaped = False
    index = open_brace
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "line_comment":
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if char == "*" and next_char == "/":
                state = "code"
                index += 1
        elif state in ("string", "char"):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                state = "code"
        elif char == "/" and next_char == "/":
            state = "line_comment"
            index += 1
        elif char == "/" and next_char == "*":
            state = "block_comment"
            index += 1
        elif char == '"':
            state = "string"
        elif char == "'":
            state = "char"
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
        index += 1
    raise AssertionError(f"unterminated function: {function_name}")


def native_cc() -> list[str]:
    configured_zig = os.environ.get("DESKBOT_ZIG")
    outer = ROOT.parent.parent
    bundled_zig = outer / "tmp/zig-x86_64-windows-0.15.2/zig.exe"
    for candidate in (Path(configured_zig) if configured_zig else None, bundled_zig):
        if candidate is not None and candidate.is_file():
            return [str(candidate.resolve()), "cc"]
    configured_cc = os.environ.get("DESKBOT_CC") or os.environ.get("CC")
    if configured_cc:
        words = configured_cc.split()
        resolved = shutil.which(words[0])
        if resolved:
            words[0] = resolved
            return words
    found = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
    if found:
        return [found]
    raise RuntimeError("C compiler required; bundled Zig 0.15.2 or native clang/gcc is not available")


def extracted_sources(driver_raw: bytes) -> tuple[str, str, str]:
    master_source = IDF_SPI_MASTER.read_text(encoding="utf-8")
    io_source = IDF_SPI_IO.read_text(encoding="utf-8")
    driver_source = driver_raw.decode("utf-8")
    adapter_path = source_path(
        "managed_components/espressif__esp_lvgl_adapter/src/display/bridge/v9/lvgl_bridge_v9.c"
    )
    adapter_source = adapter_path.read_text(encoding="utf-8")
    uninstall = extract_function(master_source, "uninstall_priv_desc")
    io_tx = extract_function(io_source, "panel_io_spi_tx_color")
    panel_draw = extract_function(driver_source, "panel_st77916_draw_bitmap")
    flush = extract_function(adapter_source, "display_bridge_v9_flush_default")
    notify_helper = extract_function(adapter_source, "display_bridge_v9_signal_dummy_draw_event")
    io_done_callback = extract_function(adapter_source, "display_bridge_v9_on_io_color_trans_done")
    dummy = extract_function(adapter_source, "display_bridge_v9_dummy_draw_blit")
    return (uninstall + "\n" + io_tx + "\n", panel_draw + "\n",
            flush + "\n" + notify_helper + "\n" + io_done_callback + "\n" + dummy + "\n")


class E3c3bDisplayDmaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for path, expected in ((IDF_SPI_MASTER, SPI_MASTER_SHA256), (IDF_SPI_IO, SPI_IO_SHA256)):
            if not path.is_file():
                raise RuntimeError(f"pinned ESP-IDF source is missing: {path}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != expected:
                raise RuntimeError(f"ESP-IDF source digest differs for {path}: {actual}")
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c3b-display-dma-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        frozen_driver_path = source_path(dma.DRIVER)
        frozen_driver = dma.check_frozen(frozen_driver_path, dma.DASH[dma.DRIVER])
        frozen_board_path = source_path(dma.BOARD)
        frozen_board = dma.check_frozen(frozen_board_path, dma.DASH[dma.BOARD])
        dma.patch_board(frozen_board)
        cls.binaries: dict[str, Path] = {}
        cc = native_cc()
        for label, driver in (("frozen", frozen_driver), ("patched", dma.patch_driver(frozen_driver))):
            idf_io, panel, adapter = extracted_sources(driver)
            (folder / "dma_idf_io.inc").write_text(idf_io, encoding="utf-8")
            (folder / "dma_panel.inc").write_text(panel, encoding="utf-8")
            (folder / "dma_adapter.inc").write_text(adapter, encoding="utf-8")
            binary = folder / f"dma-display-{label}.exe"
            command = cc + [
                "-std=c11", "-Wall", "-Wextra", "-Werror",
                f"-DTEST_PATCHED_DRIVER={1 if label == 'patched' else 0}",
                "-I", str(folder), str(HARNESS), "-o", str(binary),
            ]
            build = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            if build.returncode != 0:
                raise RuntimeError(f"native compile failed ({label}):\n{build.stdout}\n{build.stderr}")
            cls.binaries[label] = binary

    def run_harness(self, label: str) -> str:
        result = subprocess.run([str(self.binaries[label])], cwd=ROOT,
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_frozen_driver_reproduces_swallowed_error_and_wait(self) -> None:
        output = self.run_harness("frozen")
        self.assertIn("dma_display_native_tests_passed: frozen", output)
        self.assertIn("dma_display_assertions:", output)

    def test_patched_driver_propagates_error_and_recovers(self) -> None:
        output = self.run_harness("patched")
        self.assertIn("dma_display_native_tests_passed: patched", output)
        self.assertIn("dma_display_assertions:", output)

    def test_board_patch_is_exact_and_fail_closed(self) -> None:
        original = source_path(dma.BOARD).read_bytes()
        changed = dma.patch_board(original)
        newline = b"\r\n" if b"\r\n" in original else b"\n"
        first_old = b"void EspS3Cat::InitializeSpi()\n{\n    const spi_bus_config_t bus_config =".replace(b"\n", newline)
        first_new = (b"void EspS3Cat::InitializeSpi()\n{\n    constexpr int kDisplayDmaChunkBytes = 4092;\n"
                     b"    const spi_bus_config_t bus_config =").replace(b"\n", newline)
        second_old = b"                                                                              DISPLAY_WIDTH * 8 * sizeof(uint16_t));"
        second_new = b"                                                                              kDisplayDmaChunkBytes);"
        expected = original.replace(
            first_old,
            first_new,
            1,
        ).replace(
            second_old,
            second_new,
            1,
        )
        self.assertEqual(changed, expected)
        self.assertIn(b"kDisplayDmaChunkBytes = 4092", changed)
        with self.assertRaises(ValueError):
            dma.patch_board(original + b"\n// mutation\n")

    def test_driver_patch_is_exact_and_fail_closed(self) -> None:
        original = source_path(dma.DRIVER).read_bytes()
        changed = dma.patch_driver(original)
        newline = b"\r\n" if b"\r\n" in original else b"\n"
        old = b"    tx_color(st77916, io, LCD_CMD_RAMWR, color_data, len);\n\n    return ESP_OK;".replace(b"\n", newline)
        new = b"    return tx_color(st77916, io, LCD_CMD_RAMWR, color_data, len);"
        expected = original.replace(old, new, 1)
        self.assertNotEqual(expected, original)
        self.assertEqual(changed, expected)
        self.assertIn(b"return tx_color(st77916, io, LCD_CMD_RAMWR, color_data, len);", changed)
        with self.assertRaises(ValueError):
            dma.patch_driver(original + b"\n// mutation\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
