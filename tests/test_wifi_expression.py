"""Exercise the production protocol/control and actual network adapter on the host."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "firmware/body/main/boards/esp_vocat"


def compiler():
    spec = importlib.util.spec_from_file_location("compiler_helper", ROOT / "scripts/test-expression-e1-26-cpp.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    command, kind = helper.find_compiler() or (None, None)
    if command is None:
        raise RuntimeError("configured host C++ compiler required")
    return command + (["c++"] if kind == "zig-c++" else [])


def compile_native(source, output, includes=()):
    result = subprocess.run(compiler() + ["-std=c++17", "-Wall", "-Wextra", "-Werror"]
        + [item for path in includes for item in ("-I", str(path))]
        + ["-I", str(BOARD), str(source), "-o", str(output)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)


def worker_stubs():
    return {
        "sdkconfig.h": '#pragma once\n#define CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE 1\n#define CONFIG_DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE 1\n#define CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE 1\n',
        "wifi_expression.local.h": '#pragma once\ninline constexpr char kExpressionUrl[]="https://127.0.0.1/";\ninline constexpr char kExpressionAuthorization[]="Bearer '+('a'*64)+'";\ninline constexpr char kExpressionCa[]="test";\ninline constexpr char kExpressionServerName[]="test";\n',
        "application.h": '#pragma once\n#include <functional>\nclass Application{public: static Application& GetInstance(); void Schedule(std::function<void()>);};\n',
        "board.h": '#pragma once\nclass Display;\nclass Board{public:static Board& GetInstance(); Display* GetDisplay();};\n',
        "display/emote_display.h": '#pragma once\n#include "expression_emote/emote_assets.h"\nclass Display{public:virtual ~Display()=default;};\nnamespace emote {class EmoteDisplay:public Display{public:emote_handle_t GetEmoteHandle(){return this;}};}\n',
        "esp_log.h": '#pragma once\ninline void test_log(const char*, const char*, ...) {}\n#define ESP_LOGI test_log\n#define ESP_LOGW test_log\n',
        "esp_timer.h": '#pragma once\n#include <cstdint>\nint64_t esp_timer_get_time();\n',
        "freertos/FreeRTOS.h": '#pragma once\n#include <cstdint>\n#define pdPASS 1\n#define pdMS_TO_TICKS(x) (x)\n',
        "freertos/task.h": '#pragma once\n#include "FreeRTOS.h"\nvoid vTaskDelay(uint32_t);\nint xTaskCreate(void(*)(void*),const char*,unsigned,void*,unsigned,void*);\n',
        "esp_netif.h": '#pragma once\n#define ESP_OK 0\n#define ESP_FAIL -1\nstruct esp_netif_t{};\nstruct esp_netif_ip_info_t{struct{unsigned addr;}ip;};\nesp_netif_t* esp_netif_get_handle_from_ifkey(const char*);\nbool esp_netif_is_netif_up(esp_netif_t*);\nint esp_netif_get_ip_info(esp_netif_t*,esp_netif_ip_info_t*);\n',
        "esp_http_client.h": '#pragma once\n#include "esp_netif.h"\n#define HTTP_METHOD_POST 1\n#define HTTP_EVENT_ON_DATA 2\nusing esp_err_t=int;\nusing esp_http_client_handle_t=void*;\nstruct esp_http_client_event_t{int event_id;int data_len;void* data;void* user_data;};\nstruct esp_http_client_config_t{const char* url;const char* cert_pem;const char* common_name;int method;int timeout_ms;int buffer_size;int buffer_size_tx;bool disable_auto_redirect;int max_authorization_retries;bool skip_cert_common_name_check;esp_err_t(*event_handler)(esp_http_client_event_t*);void* user_data;};\nesp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t*);\nint esp_http_client_set_header(esp_http_client_handle_t,const char*,const char*);\nint esp_http_client_set_post_field(esp_http_client_handle_t,const char*,int);\nint esp_http_client_perform(esp_http_client_handle_t);\nint esp_http_client_get_status_code(esp_http_client_handle_t);\nint esp_http_client_cleanup(esp_http_client_handle_t);\n',
    }


class WifiExpressionTests(unittest.TestCase):
    def test_production_network_and_application_adapter(self):
        stubs = worker_stubs()
        with tempfile.TemporaryDirectory(prefix="wifi-expression-worker-", dir=ROOT / "tmp") as folder:
            folder = Path(folder)
            for name, text in stubs.items():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            binary = folder / "worker.exe"
            compile_native(ROOT / "tests/cpp/wifi_expression_worker_test.cc", binary,
                (folder, ROOT / "tests/cpp/layout_stubs", ROOT / "tests/cpp/catalog_stubs", ROOT / "firmware/body/main"))
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_core(self):
        with tempfile.TemporaryDirectory(prefix="wifi-expression-", dir=ROOT / "tmp") as folder:
            binary = Path(folder) / "core.exe"
            compile_native(ROOT / "tests/cpp/wifi_expression_test.cc", binary)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
