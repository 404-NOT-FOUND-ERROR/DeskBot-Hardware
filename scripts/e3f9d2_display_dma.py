"""One board-level transport change on the retained E3f-9d.1 sources."""
import hashlib

BOARD = 'main/boards/esp_vocat/esp_vocat.cc'
BOARD_SHA = 'c7f89654febdff7ee9da8ada53cd4a4dbbfbb27378ad6a404dbb069c99864706'
REPORT_SHA = '54383647d6f135d8dd2320e7d1893ea0d0988d1fc905a99a9573d57e8beeccb7'


def patch_board(raw):
    if hashlib.sha256(raw).hexdigest() != BOARD_SHA:
        raise ValueError('E3f-9d.1 board source changed')
    anchor = b'    io_config.trans_queue_depth = 2;'
    if raw.count(anchor) != 1:
        raise ValueError('display IO anchor changed')
    newline = b'\r\n' if b'\r\n' in raw else b'\n'
    # IDF keeps the existing completion ownership and cache synchronization.
    # If a PSRAM copy is required, it is allocated externally, not in SRAM.
    return raw.replace(anchor, anchor + newline +
        b'    // Send PSRAM pixels through EDMA; avoid per-chunk internal TX copies.' + newline +
        b'    io_config.flags.psram_dma_direct = true;', 1)
