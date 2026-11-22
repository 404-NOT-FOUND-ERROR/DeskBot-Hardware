"""Pinned, fail-closed E3c-3b display DMA source changes."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "tmp/e3c3a-usb-path-20261106/p"
FROZEN_BUILD = ROOT / "tmp/e3c3a-usb-path-20261106/on"
BASE_REPORT = ROOT / "artifacts/ui-menu-regression-20261106/e3c3a-build.json"
WORK = ROOT / "tmp/e3c3b-display-dma-20261107"
PUBLIC = ROOT / "artifacts/display-dma-repair-e3c3b-20261107"

DRIVER = "managed_components/espressif__esp_lcd_st77916/esp_lcd_st77916.c"
BOARD = "main/boards/esp_vocat/esp_vocat.cc"
DASH = {
    DRIVER: "a9124ac8a582d32fee40f60e6e5be747253ace5bc458d0e21972a84a416570ff",
    BOARD: "98878a2fc140c6f4a173c457042527da92fa0a0982250dabc389f18c4257d532",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def replace_once(raw, old, new):
    if raw.count(old) == 1:
        return raw.replace(old, new, 1)
    old_crlf = old.replace(b"\n", b"\r\n")
    if raw.count(old_crlf) == 1:
        return raw.replace(old_crlf, new.replace(b"\n", b"\r\n"), 1)
    raise ValueError("frozen source anchor changed")


def check_frozen(path, expected):
    raw = path.read_bytes()
    if sha(raw) != expected:
        raise ValueError("frozen source digest differs: " + path.as_posix())
    return raw


def patch_driver(raw):
    if sha(raw) != DASH[DRIVER]:
        raise ValueError("E3c-3a ST77916 driver differs")
    return replace_once(
        raw,
        b"    tx_color(st77916, io, LCD_CMD_RAMWR, color_data, len);\n\n    return ESP_OK;",
        b"    return tx_color(st77916, io, LCD_CMD_RAMWR, color_data, len);",
    )


def patch_board(raw):
    if sha(raw) != DASH[BOARD]:
        raise ValueError("E3c-3a board source differs")
    raw = replace_once(
        raw,
        b"void EspS3Cat::InitializeSpi()\n{\n    const spi_bus_config_t bus_config =",
        b"void EspS3Cat::InitializeSpi()\n{\n    constexpr int kDisplayDmaChunkBytes = 4092;\n"
        b"    const spi_bus_config_t bus_config =",
    )
    return replace_once(
        raw,
        b"                                                                              DISPLAY_WIDTH * 8 * sizeof(uint16_t));",
        b"                                                                              kDisplayDmaChunkBytes);",
    )


def candidate(raw_by_path):
    if set(raw_by_path) != set(DASH):
        raise ValueError("candidate must contain only the two reviewed display sources")
    return {DRIVER: patch_driver(raw_by_path[DRIVER]),
            BOARD: patch_board(raw_by_path[BOARD])}
