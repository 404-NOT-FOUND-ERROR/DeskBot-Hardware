"""Clock-only source change and display startup error classification."""
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from e3f9d3_display_clock import BOARD,patch_board
from protocol.display_clock_e3f9d3 import DISPLAY_ERRORS,check_display_boot


class DisplayClockTests(unittest.TestCase):
    def test_clock_is_only_source_change(self):
        raw=(ROOT/'tmp/display-psram-e3f9d2/p'/BOARD).read_bytes()
        changed=patch_board(raw)
        line=b'    io_config.pclk_hz = 20 * 1000 * 1000;'
        newline=b'\r\n' if b'\r\n' in raw else b'\n'
        self.assertEqual(changed.replace(line+newline,b'',1),raw)
        for retained in (b'kDisplayDmaChunkBytes = 4092;',
                         b'io_config.trans_queue_depth = 2;',
                         b'io_config.flags.psram_dma_direct = true;'):
            self.assertEqual(changed.count(retained),1)
        header=ROOT/'tmp/display-psram-e3f9d2/p/managed_components/espressif__esp_lcd_st77916/include/esp_lcd_st77916.h'
        self.assertIn('.pclk_hz = 40 * 1000 * 1000',header.read_text(encoding='utf-8'))

    def test_unexpected_source_and_repeated_patch_stop(self):
        raw=(ROOT/'tmp/display-psram-e3f9d2/p'/BOARD).read_bytes()
        for changed in (raw+b'\n',patch_board(raw)):
            with self.assertRaises(ValueError):patch_board(changed)

    def test_observed_display_errors_reject_candidate(self):
        self.assertTrue(check_display_boot('LCD panel create success\nnormal boot'))
        for marker in DISPLAY_ERRORS:
            with self.subTest(marker=marker),self.assertRaisesRegex(ValueError,'candidate_display_transport_failed'):
                check_display_boot('normal boot\nE '+marker+'\nnormal boot')
        log=ROOT/'tmp/e3c-live-20261113-e3f9d2-live1/captures/051-eat-boot.log'
        with self.assertRaisesRegex(ValueError,'DMA TX underflow'):
            check_display_boot(log.read_text(encoding='utf-8'))


if __name__=='__main__':unittest.main()
