"""Exercise the production activation adapter with native NVS/application seams."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "firmware/body/main/boards/esp_vocat"


class RoleActivationAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("adapter_compiler", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        found = helper.find_compiler()
        if not found:
            raise RuntimeError("C++17 compiler required")
        command, kind = found
        (ROOT / "tmp").mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix="role-adapter-", dir=ROOT / "tmp")
        cls.addClassCleanup(cls.temp.cleanup)
        folder = Path(cls.temp.name)
        (folder / "display").mkdir()
        (folder / "expression_emote").mkdir()
        stubs = {
            "sdkconfig.h": "#pragma once\n#define CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE 1\n",
            "application.h": """#pragma once
#include <functional>
class Application { public:
    static Application& GetInstance();
    void Schedule(std::function<void()> callback);
};
""",
            "board.h": """#pragma once
class Display;
class Board { public: static Board& GetInstance(); Display* GetDisplay(); };
""",
            "display/emote_display.h": """#pragma once
#include "expression_emote/emote_assets.h"
class Display { public: virtual ~Display() = default; };
namespace emote { class EmoteDisplay : public Display {
public: explicit EmoteDisplay(emote_handle_t handle) : handle_(handle) {}
    emote_handle_t GetEmoteHandle() const { return handle_; }
private: emote_handle_t handle_;
}; }
""",
            # Match the actual opaque SDK pointer. A void* alias would hide
            # missing conversions at the adapter's protocol-only boundary.
            "expression_emote/emote_assets.h": """#pragma once
#include "esp_partition.h"
struct emote_s;
using emote_handle_t = emote_s*;
struct emoji_data_t { const void* data; size_t size; uint8_t fps; bool loop; };
struct icon_data_t { const void* data; size_t size; };
esp_err_t emote_get_asset_data_by_name(emote_handle_t, const char*, const uint8_t**, size_t*);
esp_err_t emote_get_icon_data_by_name(emote_handle_t, const char*, icon_data_t**);
esp_err_t emote_get_emoji_data_by_name(emote_handle_t, const char*, emoji_data_t**);
""",
            "nvs.h": """#pragma once
#include "esp_partition.h"
using nvs_handle_t = uint32_t;
constexpr int NVS_READONLY = 0, NVS_READWRITE = 1;
esp_err_t nvs_open(const char*, int, nvs_handle_t*);
esp_err_t nvs_get_blob(nvs_handle_t, const char*, void*, size_t*);
esp_err_t nvs_set_blob(nvs_handle_t, const char*, const void*, size_t);
esp_err_t nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
""",
        }
        for name, text in stubs.items():
            (folder / name).write_text(text, encoding="utf-8")
        cls.exe = folder / ("adapter.exe" if os.name == "nt" else "adapter")
        flags = ["-UNDEBUG", "-DCONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE=1"]
        for include in (folder, ROOT / "tests/cpp/layout_stubs", ROOT / "tests/cpp/catalog_stubs",
                        BOARD, ROOT / "firmware/body/main"):
            flags.extend(("-I", str(include)))
        result = subprocess.run(command + (["c++"] if kind == "zig-c++" else []) + flags + [
            "-std=c++17", "-Wall", "-Wextra", "-Werror", "-pthread",
            str(ROOT / "tests/cpp/studio_role_activation_adapter_test.cc"), "-o", str(cls.exe)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)

    def run_case(self, mode):
        run = subprocess.run([str(self.exe), mode], cwd=ROOT, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("adapter_pass " + mode, run.stdout)

    def test_queued_dispatch_waits_for_matching_frame_before_save(self):
        self.run_case("queue-frame-save")

    def test_nvs_open_set_and_commit_failures_allow_same_role_retry(self):
        for mode in ("open-failure", "set-failure", "commit-failure"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_saved_role_restores_at_mount_without_rewriting_nvs(self):
        self.run_case("restore")

    def test_invalid_or_missing_saved_blob_uses_package_default(self):
        for mode in ("get-failure", "blob-size", "blob-version", "blob-graph", "blob-role"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_disconnect_or_expiry_cancels_queued_selection(self):
        for mode in ("disconnect", "expiry"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_newest_queued_target_replaces_previous_work(self):
        self.run_case("latest")

    def test_graph_or_mount_change_cancels_old_work(self):
        for mode in ("graph-change", "mount-change"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_missing_display_and_memory_failure_are_recoverable(self):
        for mode in ("missing-display", "allocation-failure"):
            with self.subTest(mode=mode):
                self.run_case(mode)

    def test_new_target_can_queue_during_previous_nvs_commit(self):
        self.run_case("request-during-save")

    def test_player_failure_or_legacy_takeover_finishes_attempt(self):
        for mode in ("frame-failure", "interrupted"):
            with self.subTest(mode=mode):
                self.run_case(mode)


if __name__ == "__main__":
    unittest.main()
