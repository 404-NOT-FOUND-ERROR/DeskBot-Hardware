"""Native integration of frozen display snapshots and the accepted USB task."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from protocol import studio_display_query as display
from protocol import studio_runtime_query as runtime
from protocol import studio_usb_session as session

ROOT = Path(__file__).resolve().parents[1]


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


class RetainedDisplayUsbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        import e3c4a_display as candidate
        sources, _ = candidate.candidate_sources()
        # Release checks compile the same harness against the staged source.
        # Existing callers keep testing the historical candidate.
        if "DESKBOT_NATIVE_SOURCE_ROOT" in os.environ:
            staged = Path(os.environ["DESKBOT_NATIVE_SOURCE_ROOT"])
            sources = {name: (staged / name).read_bytes() for name in sources}
        compiler = module("e3c4a_compiler", "scripts/test-expression-e1-26-cpp.py")
        command, kind = compiler.find_compiler() or (None, None)
        if command is None:
            raise RuntimeError("C++17 compiler required")
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c4a-native-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        for name, raw in sources.items():
            if name.startswith("main/") and (name.endswith(".h") or name.endswith("studio_runtime_state.cc") or name.endswith("studio_usb_diagnostic.cc")):
                path = folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        board = folder / "main/boards/esp_vocat"
        for name, text in {
            "driver/usb_serial_jtag.h": "bool usb_serial_jtag_is_driver_installed();\nbool usb_serial_jtag_is_connected();\n",
            "esp_log.h": "void test_log(const char*, const char*, ...);\n#define ESP_LOGI test_log\n#define ESP_LOGW test_log\n",
            "esp_timer.h": "#include <cstdint>\nint64_t esp_timer_get_time();\n",
            "freertos/FreeRTOS.h": "#pragma once\n#define pdPASS 1\n#define pdMS_TO_TICKS(x) (x)\n",
            "freertos/task.h": "void vTaskDelete(void*);\nvoid vTaskDelay(unsigned);\nint xTaskCreate(void (*)(void*), const char*, unsigned, void*, unsigned, void*);\n",
            "freertos/portmacro.h": """#pragma once
#include <mutex>
#include <cassert>
struct portMUX_TYPE { std::mutex mutex; };
#define portMUX_INITIALIZER_UNLOCKED {}
inline thread_local bool critical_entered = false;
inline void portENTER_CRITICAL(portMUX_TYPE* mux) {
    assert(!critical_entered); mux->mutex.lock(); critical_entered = true;
}
inline void portEXIT_CRITICAL(portMUX_TYPE* mux) {
    assert(critical_entered); critical_entered = false; mux->mutex.unlock();
}
""",
        }.items():
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        cls.binaries = {}
        for enabled in (False, True):
            (folder / "sdkconfig.h").write_text("#pragma once\n" + "\n".join([
                "#define CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE 1",
                "#define CONFIG_DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE 1",
                "#define CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE 1",
                "#define CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG 1",
                f"#define CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE {int(enabled)}", ""]), encoding="utf-8")
            executable = folder / f"display-{int(enabled)}.exe"
            result = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
                "-std=c++17", "-Wall", "-Wextra", "-Werror", "-UNDEBUG", "-pthread",
                "-include", str(folder / "sdkconfig.h"), "-I", str(folder), "-I", str(folder / "main"), "-I", str(board),
                str(ROOT / "tests/cpp/studio_display_usb_test.cc"), str(board / "studio_runtime_state.cc"),
                "-o", str(executable)], cwd=ROOT, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stdout + result.stderr)
            cls.binaries[enabled] = executable

    def run_task(self, mode="normal", enabled=True):
        result = subprocess.run([str(self.binaries[enabled]), mode], capture_output=True, text=True, check=True, timeout=10)
        rows = result.stdout.splitlines()
        self.assertIn("USB input ready: /dev/secondary", rows)
        self.assertIn("USB input owner changed", rows)
        replies = [json.loads(line) for line in rows if line.startswith("{")]
        validators = {display.SCHEMA: display.validate_reply, runtime.SCHEMA: runtime.validate_reply,
                      session.SCHEMA: session.validate_reply}
        for reply in replies:
            validators[reply["schema"]](reply)
        return rows, replies

    def test_display_off_preserves_state_and_rejects_display(self):
        _, replies = self.run_task(enabled=False)
        self.assertEqual(replies[1]["reason"], "unsupported_operation")
        self.assertEqual(replies[2]["schema"], runtime.SCHEMA)

    def test_display_on_binds_real_snapshot_provider(self):
        _, replies = self.run_task()
        self.assertEqual([row["request_id"] for row in replies], ["open", "display", "state"])
        self.assertEqual(replies[1]["player"], {"revision": 1, "state": "initialized"})
        self.assertEqual(replies[1]["preview"], {"available": False, "reason": "sleep_unknown"})
        self.assertEqual(replies[1]["application"], replies[2]["application"])

    def test_menu_snapshot_returns_not_home(self):
        self.assertEqual(self.run_task("menu")[1][1]["preview"]["reason"], "not_home")

    def test_speaking_snapshot_returns_busy(self):
        self.assertEqual(self.run_task("busy")[1][1]["preview"]["reason"], "application_busy")

    def test_unknown_player_does_not_claim_ready(self):
        self.assertEqual(self.run_task("unknown")[1][1]["preview"]["reason"], "player_unknown")

    def test_stopping_player_does_not_claim_ready(self):
        self.assertEqual(self.run_task("stopping")[1][1]["preview"]["reason"], "player_stopping")

    def test_queries_require_an_open_session(self):
        _, replies = self.run_task("no_session")
        self.assertTrue(all(row["schema"] == session.SCHEMA and row["state"] == "rejected" for row in replies))

    def test_single_byte_reads_complete_without_changing_yield(self):
        rows, replies = self.run_task("single")
        self.assertEqual([row["request_id"] for row in replies], ["open", "display", "state"])
        self.assertGreater(int(rows[-1].split("elapsed_ms=")[1]), 8000)

    def test_continuous_input_remains_one_read_per_tick(self):
        rows, replies = self.run_task("flood")
        self.assertEqual((rows[-1], replies), ("ticks=30 elapsed_ms=300", []))


if __name__ == "__main__":
    unittest.main()
