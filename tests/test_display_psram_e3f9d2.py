"""Real IDF setup/heap-normalization/LCD-queue functions under heap pressure."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from e3f9d2_display_dma import BOARD,patch_board
spec=importlib.util.spec_from_file_location('existing_dma_native',ROOT/'tests/test_e3c3b_display_dma.py')
existing=importlib.util.module_from_spec(spec);spec.loader.exec_module(existing)
IDF=existing.IDF_ROOT
SOURCES={
    'esp_driver_spi/src/gpspi/spi_master.c':existing.SPI_MASTER_SHA256,
    'esp_lcd/spi/esp_lcd_panel_io_spi.c':existing.SPI_IO_SHA256,
    'esp_mm/heap_align_hw.c':'ea0efd00176a5aad2a65bd70636bb0b1db4e6015e831e9e7e3ffd090ff50db08',
}


class PsramDisplayTests(unittest.TestCase):
    def test_exact_board_increment_preserves_transport_and_interactions(self):
        raw=(ROOT/'tmp/studio-role-activation-e3f9d/p'/BOARD).read_bytes()
        changed=patch_board(raw)
        self.assertEqual(changed.count(b'io_config.flags.psram_dma_direct = true;'),1)
        self.assertIn(b'kDisplayDmaChunkBytes = 4092;',changed)
        self.assertIn(b'io_config.trans_queue_depth = 2;',changed)
        with self.assertRaises(ValueError):patch_board(changed)
        with self.assertRaises(ValueError):patch_board(raw+b'\n')

    def test_real_idf_functions_under_internal_heap_pressure(self):
        texts={}
        for name,digest in SOURCES.items():
            raw=(IDF/'components'/name).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(),digest)
            texts[name]=raw.decode('utf-8')
        functions={
            'psram_dma_setup.inc':('esp_driver_spi/src/gpspi/spi_master.c','setup_dma_priv_buffer'),
            'psram_dma_release.inc':('esp_driver_spi/src/gpspi/spi_master.c','uninstall_priv_desc'),
            'psram_lcd_io.inc':('esp_lcd/spi/esp_lcd_panel_io_spi.c','panel_io_spi_tx_color'),
            'psram_heap_adjust.inc':('esp_mm/heap_align_hw.c','esp_heap_adjust_alignment_to_hw'),
        }
        with tempfile.TemporaryDirectory(prefix='psram-dma-',dir=ROOT/'tmp') as name:
            folder=Path(name)
            for output,(source,function) in functions.items():
                text=existing.extract_function(texts[source],function)
                if output=='psram_heap_adjust.inc':
                    text='#define CAPS_NEEDING_ALIGNMENT (MALLOC_CAP_DMA|MALLOC_CAP_DMA_DESC_AHB|MALLOC_CAP_DMA_DESC_AXI|MALLOC_CAP_CACHE_ALIGNED|MALLOC_CAP_SIMD)\n'+text
                (folder/output).write_text(text,encoding='utf-8')
            binary=folder/'psram-dma.exe'
            # IDF represents DMA byte addresses as uint32_t*. Its real S3 path
            # permits TX alignment=1 without a CPU word load. Keep those source
            # functions intact; disable only the host typed-pointer alignment
            # sanitizer. Bounds, contents, lifetimes and all C asserts remain.
            command=existing.native_cc()+['-std=c11','-Wall','-Wextra','-Werror','-Wno-pointer-to-int-cast','-fno-sanitize=alignment',
                '-I',str(folder),str(ROOT/'tests/cpp/psram_display_dma_harness.c'),'-o',str(binary)]
            compiled=subprocess.run(command,cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(compiled.returncode,0,compiled.stdout+compiled.stderr)
            result=subprocess.run([str(binary)],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('psram_dma_native_passed: 97 full-content cases;',result.stdout)


if __name__=='__main__':unittest.main()
