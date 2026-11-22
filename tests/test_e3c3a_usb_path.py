"""Run the frozen and path-corrected E3c-3 USB tasks with host VFS stubs."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from protocol import studio_usb_session as contract

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "tmp/e3c3-state-20261105/p/main/boards/esp_vocat"


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class RetainedUsbPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        patch = module("e3c3a_path_patch", "scripts/e3c3a_usb_path.py")
        compiler = module("e3c3a_compiler", "scripts/test-expression-e1-26-cpp.py")
        command, kind = compiler.find_compiler() or (None, None)
        if command is None:
            raise RuntimeError("C++17 compiler required")
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c3a-usb-test-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        for name, content in {
            "sdkconfig.h": "#define CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE 1\n",
            "driver/usb_serial_jtag.h": "bool usb_serial_jtag_is_driver_installed();\nbool usb_serial_jtag_is_connected();\n",
            "esp_log.h": "void test_log(const char*, const char*, ...);\n#define ESP_LOGI test_log\n#define ESP_LOGW test_log\n",
            "esp_timer.h": "#include <cstdint>\nint64_t esp_timer_get_time();\n",
            "freertos/FreeRTOS.h": "#define pdPASS 1\n#define pdMS_TO_TICKS(x) (x)\n",
            "freertos/task.h": "void vTaskDelete(void*);\nvoid vTaskDelay(unsigned);\nint xTaskCreate(void (*)(void*), const char*, unsigned, void*, unsigned, void*);\n",
        }.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        original = (BOARD / "studio_usb_diagnostic.cc").read_bytes()
        (folder / "historical.cc").write_bytes(original)
        (folder / "candidate.cc").write_bytes(patch.candidate_bytes(original))
        source = folder / "test.cc"
        source.write_text(r'''
#include <cassert>
#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <fcntl.h>
#include <unistd.h>
static bool owned = false, changed = false, no_data = false, flood = false, single = false;
static bool finished = false, fail_open = false;
static unsigned opens = 0, closes = 0, delays = 0, reads = 0, replies = 0;
static std::string incoming;
static int test_open(const char* path, int flags) {
    ++opens;
    assert(flags == O_RDONLY);
#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
    const bool registered = std::strcmp(path, "/dev/secondary") == 0;
#else
    const bool registered = std::strcmp(path, "/dev/usbserjtag") == 0;
#endif
    if (!registered || fail_open) { errno = ENOENT; return -1; }
    return 42;
}
static ssize_t test_read(int fd, void* data, size_t capacity) {
    assert(fd == 42 && capacity == 64 && ++reads == 1);
    if (no_data) return -1;
    if (flood) { std::memset(data, 'x', capacity); return capacity; }
    const size_t limit = single ? 1 : capacity;
    const size_t count = incoming.size() < limit ? incoming.size() : limit;
    std::memcpy(data, incoming.data(), count);
    incoming.erase(0, count);
    return count;
}
static int test_close(int fd) { assert(fd == 42); ++closes; return 0; }
#define open test_open
#define read test_read
#define close test_close
#if HISTORICAL
#include "historical.cc"
#else
#include "candidate.cc"
#endif
#undef open
#undef read
#undef close
void test_log(const char*, const char* format, ...) {
    char buffer[1100]; va_list args; va_start(args, format);
    std::vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    if (buffer[0] == '{') ++replies;
    std::puts(buffer);
}
int64_t esp_timer_get_time() { return 1000000 + delays * 10000; }
bool usb_serial_jtag_is_driver_installed() { return owned || changed; }
bool usb_serial_jtag_is_connected() { return true; }
void vTaskDelete(void*) { finished = true; }
void vTaskDelay(unsigned ticks) {
    assert(ticks == 10 && reads == 1);
    reads = 0;
    ++delays;
    if ((no_data || flood) ? delays == 10 : incoming.empty()) changed = true;
    assert(delays < 1000);
}
int xTaskCreate(void (*task)(void*), const char*, unsigned stack, void* context, unsigned priority, void*) {
    assert(stack == 6144 && priority == 1);
    task(context);
    return 1;
}
int main(int argc, char** argv) {
    assert(argc == 2);
    owned = std::strcmp(argv[1], "owned") == 0;
    fail_open = std::strcmp(argv[1], "missing") == 0;
    no_data = std::strcmp(argv[1], "empty") == 0;
    flood = std::strcmp(argv[1], "flood") == 0;
    single = std::strcmp(argv[1], "single") == 0;
    incoming = "{\"schema\":\"deskbot.studio_session/v1\",\"operation\":\"open\",\"request_id\":\"r1\","
        "\"target_id\":\"device-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"boot_id\":\"boot-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\","
        "\"client_nonce\":\"client-a\",\"session_id\":null}\n";
    const auto bytes = incoming.size();
    deskbot_studio_session::Start("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    assert(opens == (owned ? 0U : 1U));
#if HISTORICAL && CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
    assert(finished && closes == 0 && delays == 0 && replies == 0);
#else
    if (owned) assert(!finished && closes == 0 && delays == 0);
    else if (fail_open) assert(finished && closes == 0 && delays == 0 && replies == 0);
    else {
        assert(finished && closes == 1);
        if (no_data || flood) assert(delays == 10 && replies == 0);
        else {
            assert(incoming.empty() && replies == 1);
            assert(delays == (single ? bytes : (bytes + 63) / 64));
        }
    }
#endif
    std::printf("ticks=%u elapsed_ms=%u\n", delays, delays * 10);
}
''', encoding="utf-8")
        cls.binaries = {}
        for historical, secondary in ((True, True), (False, True), (False, False)):
            executable = folder / f"task-{int(historical)}-{int(secondary)}.exe"
            built = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
                "-std=c++17", "-Wall", "-Wextra", "-Werror", "-UNDEBUG", "-Wno-unused-variable",
                f"-DHISTORICAL={int(historical)}", f"-DCONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG={int(secondary)}",
                f"-DCONFIG_ESP_CONSOLE_USB_SERIAL_JTAG={int(not secondary)}",
                "-I", str(folder), "-I", str(BOARD), str(source), "-o", str(executable)],
                cwd=ROOT, capture_output=True, text=True, timeout=180)
            if built.returncode:
                raise RuntimeError(built.stdout + built.stderr)
            cls.binaries[historical, secondary] = executable

    def task(self, mode="normal", historical=False, secondary=True):
        run = subprocess.run([str(self.binaries[historical, secondary]), mode],
                             capture_output=True, text=True, timeout=10, check=True)
        return run.stdout.splitlines()

    def test_historical_secondary_path_exits_before_receiving(self):
        self.assertEqual(self.task(historical=True), ["USB input unavailable", "ticks=0 elapsed_ms=0"])

    def test_corrected_secondary_reads_session(self):
        rows = self.task()
        reply = contract.validate_reply(json.loads(next(row for row in rows if row.startswith("{"))))
        self.assertEqual(reply["reason"], "session_opened")
        self.assertIn("USB input ready: /dev/secondary", rows)

    def test_corrected_primary_selects_primary_path(self):
        self.assertIn("USB input ready: /dev/usbserjtag", self.task(secondary=False))

    def test_failed_open_has_path_and_errno(self):
        self.assertTrue(self.task("missing")[0].startswith("USB input unavailable: /dev/secondary (errno="))

    def test_existing_driver_keeps_ownership(self):
        self.assertEqual(self.task("owned"), ["USB input configuration unavailable", "ticks=0 elapsed_ms=0"])

    def test_idle_receiver_yields_and_closes_on_owner_change(self):
        rows = self.task("empty")
        self.assertIn("USB input owner changed", rows)
        self.assertEqual(rows[-1], "ticks=10 elapsed_ms=100")

    def test_flood_preserves_single_read_then_yield(self):
        self.assertEqual(self.task("flood")[-1], "ticks=10 elapsed_ms=100")

    def test_single_byte_io_keeps_historical_timing(self):
        rows = self.task("single")
        reply = contract.validate_reply(json.loads(next(row for row in rows if row.startswith("{"))))
        self.assertEqual(reply["reason"], "session_opened")
        elapsed = int(rows[-1].split("elapsed_ms=")[1])
        self.assertGreater(elapsed, 2000)


if __name__ == "__main__":
    unittest.main()
