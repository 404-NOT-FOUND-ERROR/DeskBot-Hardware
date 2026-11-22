"""Reduce only the QSPI clock on the retained PSRAM DMA candidate."""
import hashlib

BOARD = 'main/boards/esp_vocat/esp_vocat.cc'
BOARD_SHA = 'b1f76765b2d3932dcef35a7216ba1dc6fe8de0ef7ad337f0e4371806f91a3585'
REPORT_SHA = 'bdffae6ce5b67e801a4b74215e42f78daeb7b9ba76532de12f4bb85edd5f44da'


def patch_board(raw):
    if hashlib.sha256(raw).hexdigest() != BOARD_SHA:
        raise ValueError('E3f-9d.2 board source changed')
    anchor = b'    io_config.trans_queue_depth = 2;'
    if raw.count(anchor) != 1 or b'io_config.pclk_hz =' in raw:
        raise ValueError('display clock anchor changed')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    # Lower consumption at the LCD interface; retain DMA ownership and chunks.
    return raw.replace(anchor, anchor + newline +
        b'    io_config.pclk_hz = 20 * 1000 * 1000;', 1)
