"""Run the production USB task against the two IDF console VFS registrations."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from protocol import studio_usb_session as contract

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "firmware/body/main/boards/esp_vocat"


class UsbTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("usb_transport_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        if command is None:
            raise RuntimeError("C++17 compiler required")
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="studio-usb-transport-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        stubs = {
            "sdkconfig.h": "#define CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE 1\n",
            "driver/usb_serial_jtag.h": "bool usb_serial_jtag_is_driver_installed();\nbool usb_serial_jtag_is_connected();\n",
            "esp_log.h": "void test_log(const char*, const char*, ...);\n#define ESP_LOGI test_log\n#define ESP_LOGW test_log\n",
            "esp_timer.h": "#include <cstdint>\nint64_t esp_timer_get_time();\n",
            "freertos/FreeRTOS.h": "#define pdPASS 1\n#define pdMS_TO_TICKS(x) (x)\n",
            "freertos/task.h": "void vTaskDelete(void*);\nvoid vTaskDelay(unsigned);\nint xTaskCreate(void (*)(void*), const char*, unsigned, void*, unsigned, void*);\n",
        }
        for name, value in stubs.items():
            path = cls.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        source = cls.folder / "task.cc"
        source.write_text(r'''
#include <cassert>
#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <fcntl.h>
#include <unistd.h>
static bool owner_changed = false;
static bool finished = false;
static bool fail_open = false;
static bool installed_initially = false;
static unsigned opened = 0, closed = 0, delays = 0;
static unsigned reads_this_tick = 0, bytes_this_tick = 0, max_reads = 0, max_bytes = 0;
static unsigned reply_count = 0;
static bool one_byte = false, uneven = false, flood = false, no_data = false;
static std::string incoming;
static int test_open(const char* path, int flags) {
    ++opened;
    assert(flags == O_RDONLY);
#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
    assert(std::strcmp(path, "/dev/secondary") == 0);
#else
    assert(std::strcmp(path, "/dev/usbserjtag") == 0);
#endif
    if (fail_open) { errno = ENOENT; return -1; }
    return 42;
}
static ssize_t test_read(int fd, void* buffer, size_t capacity) {
    assert(fd == 42 && capacity > 0 && capacity <= 64);
    ++reads_this_tick;
    assert(reads_this_tick <= 512);
    if (no_data) { errno = EWOULDBLOCK; return -1; }
    if (flood) {
        const size_t count = one_byte ? 1 : (uneven && capacity > 17 ? 17 : capacity);
        std::memset(buffer, 'x', count);
        bytes_this_tick += count;
        return static_cast<ssize_t>(count);
    }
    const size_t limit = one_byte ? 1 : capacity;
    const size_t size = incoming.size() < limit ? incoming.size() : limit;
    bytes_this_tick += size;
    std::memcpy(buffer, incoming.data(), size);
    incoming.erase(0, size);
    return static_cast<ssize_t>(size);
}
static int test_close(int fd) { assert(fd == 42); ++closed; return 0; }
#define open test_open
#define read test_read
#define close test_close
#include "studio_usb_diagnostic.cc"
#undef open
#undef read
#undef close
void test_log(const char*, const char* format, ...) {
    char output[1100]; va_list args; va_start(args, format);
    std::vsnprintf(output, sizeof(output), format, args); va_end(args);
    if (output[0] == '{') ++reply_count;
    std::puts(output);
}
int64_t esp_timer_get_time() { return 1000000 + delays * 10000; }
bool usb_serial_jtag_is_driver_installed() { return installed_initially || owner_changed; }
bool usb_serial_jtag_is_connected() { return true; }
void vTaskDelete(void*) { finished = true; }
void vTaskDelay(unsigned ticks) {
    assert(ticks == 10);
    max_reads = reads_this_tick > max_reads ? reads_this_tick : max_reads;
    max_bytes = bytes_this_tick > max_bytes ? bytes_this_tick : max_bytes;
    assert(reads_this_tick <= 512 && bytes_this_tick <= 512);
    reads_this_tick = bytes_this_tick = 0;
    ++delays;
    if (incoming.empty() || ((flood || no_data) && delays == 3)) owner_changed = true;
    assert(delays < 100);
}
int xTaskCreate(void (*task)(void*), const char*, unsigned stack, void* context, unsigned priority, void*) {
    assert(stack == 6144 && priority == 1);
    task(context);
    return 1;
}
int main(int argc, char** argv) {
    assert(argc == 2);
    fail_open = std::strcmp(argv[1], "missing") == 0;
    installed_initially = std::strcmp(argv[1], "owned") == 0;
    one_byte = std::strcmp(argv[1], "single-byte") == 0 || std::strcmp(argv[1], "flood-byte") == 0;
    uneven = std::strcmp(argv[1], "flood-uneven") == 0;
    flood = uneven || std::strcmp(argv[1], "flood") == 0 || std::strcmp(argv[1], "flood-byte") == 0;
    no_data = std::strcmp(argv[1], "empty") == 0;
    incoming = "{\"schema\":\"deskbot.studio_session/v1\",\"operation\":\"open\",\"request_id\":\"r1\","
        "\"target_id\":\"device-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"boot_id\":\"boot-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
        "\"client_nonce\":\"client-a\",\"session_id\":null}\n";
    deskbot_studio_session::Start("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    assert(opened == (installed_initially ? 0U : 1U));
    if (installed_initially) { assert(!finished && closed == 0 && delays == 0); }
    else if (fail_open) { assert(finished && closed == 0 && delays == 0); }
    else {
        assert(finished && closed == 1);
        if (flood) { assert(delays == 3 && max_bytes == 512); }
        else if (no_data) { assert(delays == 3 && max_reads == 1 && reply_count == 0); }
        else { assert(delays == 1 && incoming.empty() && reply_count == 1); }
        std::printf("budget: reads=%u bytes=%u ticks=%u\n", max_reads, max_bytes, delays);
    }
    return 0;
}
''', encoding="utf-8")
        cls.executables = {}
        for secondary in (False, True):
            executable = cls.folder / ("secondary.exe" if secondary else "primary.exe")
            flags = [f"-DCONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG={int(secondary)}",
                     f"-DCONFIG_ESP_CONSOLE_USB_SERIAL_JTAG={int(not secondary)}"]
            build = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
                "-std=c++17", "-Wall", "-Wextra", "-Werror", "-UNDEBUG", *flags,
                "-I", str(cls.folder), "-I", str(BOARD), str(source), "-o", str(executable)],
                cwd=ROOT, capture_output=True, text=True, timeout=180)
            if build.returncode:
                raise RuntimeError(build.stdout + build.stderr)
            cls.executables[secondary] = executable

    def run_task(self, secondary=True, mode="normal"):
        result = subprocess.run([str(self.executables[secondary]), mode], capture_output=True,
                                text=True, timeout=10, check=True)
        return result.stdout.splitlines()

    def test_secondary_console_reads_fragmented_session_and_replies(self):
        rows = self.run_task()
        reply = contract.validate_reply(json.loads(next(row for row in rows if row.startswith("{"))))
        self.assertEqual(reply["state"], "ready")
        self.assertIn("USB input ready: /dev/secondary", rows)
        self.assertIn("USB input owner changed", rows)

    def test_primary_console_reads_session_and_replies(self):
        rows = self.run_task(secondary=False)
        reply = contract.validate_reply(json.loads(next(row for row in rows if row.startswith("{"))))
        self.assertEqual(reply["state"], "ready")
        self.assertIn("USB input ready: /dev/usbserjtag", rows)

    def test_missing_vfs_reports_error_and_exits(self):
        rows = self.run_task(mode="missing")
        self.assertTrue(any(row.startswith("USB input unavailable (") for row in rows))
        self.assertFalse(any(row.startswith("{") for row in rows))

    def test_existing_driver_owner_prevents_open(self):
        self.assertEqual(self.run_task(mode="owned"), ["USB input configuration unavailable"])

    def test_basic_vfs_single_byte_reads_complete_session_in_one_tick(self):
        rows = self.run_task(mode="single-byte")
        reply = contract.validate_reply(json.loads(next(row for row in rows if row.startswith("{"))))
        self.assertEqual(reply["reason"], "session_opened")
        self.assertTrue(rows[-1].endswith("ticks=1"))

    def test_continuous_single_byte_input_yields_at_fixed_budget(self):
        self.assertIn("budget: reads=512 bytes=512 ticks=3", self.run_task(mode="flood-byte"))

    def test_continuous_buffered_input_yields_at_fixed_budget(self):
        self.assertIn("budget: reads=8 bytes=512 ticks=3", self.run_task(mode="flood"))

    def test_uneven_reads_respect_remaining_byte_budget(self):
        self.assertIn("budget: reads=31 bytes=512 ticks=3", self.run_task(mode="flood-uneven"))

    def test_empty_vfs_yields_after_one_read(self):
        self.assertIn("budget: reads=1 bytes=0 ticks=3", self.run_task(mode="empty"))


if __name__ == "__main__":
    unittest.main()
