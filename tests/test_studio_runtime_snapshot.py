"""Run the production snapshot core and adapter with a host critical-section shim."""
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "firmware/body/main"
BOARD = MAIN / "boards/esp_vocat"


class RuntimeSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("runtime_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        found = helper.find_compiler()
        if found is None:
            raise RuntimeError("C++17 compiler required")
        command, kind = found
        cls.temp = tempfile.TemporaryDirectory(prefix="e3c2-snapshot-test-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        (folder / "freertos").mkdir()
        (folder / "sdkconfig.h").write_text("#define CONFIG_DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE 1\n")
        (folder / "freertos/FreeRTOS.h").write_text("#pragma once\n")
        (folder / "freertos/portmacro.h").write_text("""#pragma once
#include <mutex>
#include <cstdlib>
struct portMUX_TYPE { std::mutex mutex; };
#define portMUX_INITIALIZER_UNLOCKED {}
inline thread_local bool critical_entered = false;
inline void portENTER_CRITICAL(portMUX_TYPE* mux) {
    if (critical_entered) std::abort();
    mux->mutex.lock(); critical_entered = true;
}
inline void portEXIT_CRITICAL(portMUX_TYPE* mux) {
    if (!critical_entered) std::abort();
    critical_entered = false; mux->mutex.unlock();
}
""")
        cls.exe = folder / "runtime.exe"
        result = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti", "-pthread",
            "-I", str(folder), "-I", str(MAIN), "-I", str(BOARD),
            str(ROOT / "tests/cpp/studio_runtime_snapshot_test.cc"), str(BOARD / "studio_runtime_state.cc"),
            "-o", str(cls.exe)], cwd=ROOT, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def run_case(self, name):
        result = subprocess.run([str(self.exe), name], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "passed")

    def test_initial_state(self): self.run_case("initial")
    def test_all_application_states(self): self.run_case("application")
    def test_unknown_application(self): self.run_case("unknown")
    def test_pages(self): self.run_case("page")
    def test_transition_hides_previous_value(self): self.run_case("transition")
    def test_overlapping_transitions(self): self.run_case("overlap")
    def test_reverse_completion_order(self): self.run_case("reverse")
    def test_independent_owners(self): self.run_case("independent")
    def test_duplicate_completion(self): self.run_case("duplicate")
    def test_foreign_ticket(self): self.run_case("foreign")
    def test_writer_capacity(self): self.run_case("capacity")
    def test_revision_exhaustion(self): self.run_case("exhaustion")
    def test_reads_preserve_state(self): self.run_case("read")
    def test_production_adapter(self): self.run_case("adapter")
    def test_scope_exit(self): self.run_case("early_return")
    def test_concurrent_writers_and_reader(self): self.run_case("concurrent")
    def test_bounded_memory(self): self.run_case("bounds")

    def test_owner_changes_are_additive(self):
        previous = json.loads((ROOT / "artifacts/deskbot-studio-e3c-20261104/firmware-build-report.json").read_bytes())["source_sha256"]
        for name in ("main/application.cc", "main/boards/esp_vocat/ui_bridge.cc"):
            current = (ROOT / "firmware/body" / name).read_text(encoding="utf-8")
            stripped = re.sub(r"#if CONFIG_DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE\n.*?#endif\n", "", current, flags=re.S)
            hashes = {hashlib.sha256(text.encode("utf-8")).hexdigest()
                      for text in (stripped, stripped.replace("\n", "\r\n"))}
            self.assertIn(previous[name], hashes, name)

    def test_page_publication_covers_refresh(self):
        text = (BOARD / "ui_bridge.cc").read_text(encoding="utf-8")
        body = text.split("void ui_bridge_switch_page(const char *page_id)", 1)[1].split("const char *ui_bridge_get_current_page", 1)[0]
        self.assertLess(body.index("PageTransition studio_transition"), body.index("s_current_page = page_id"))
        self.assertLess(body.index("s_current_page = page_id"), body.index("esp_lv_adapter_lock(-1)"))
        self.assertLess(body.index("esp_lv_adapter_unlock()"), body.index("ui_bridge_refresh_emote_display()"))
        self.assertIn("studio_transition.SetPage", body)

    def test_snapshot_has_no_hardware_or_application_calls(self):
        text = (BOARD / "studio_runtime_state.cc").read_text(encoding="utf-8")
        core = (BOARD / "studio_runtime_snapshot.h").read_text(encoding="utf-8")
        for forbidden in ("Application::", "lv_obj", "esp_lv_adapter", "SetEmotion", "vTaskDelay", "esp_timer", "new ", "malloc(", "Schedule("):
            self.assertNotIn(forbidden, text + core)
        self.assertIn("portENTER_CRITICAL(&mux)", text)
        self.assertIn("portEXIT_CRITICAL(&mux)", text)
        adapter = (BOARD / "studio_usb_diagnostic.cc").read_text()
        self.assertIn("#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE\n    endpoint.SetStateProvider(deskbot_studio_runtime::Read);", adapter)

    def test_snapshot_option_is_default_off(self):
        text = (MAIN / "Kconfig.projbuild").read_text(encoding="utf-8")
        stanza = text.split("config DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE", 1)[1].split("\nconfig ", 1)[0]
        self.assertIn("depends on DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE", stanza)
        self.assertIn("default n", stanza)


if __name__ == "__main__":
    unittest.main()
