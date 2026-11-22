"""Compile the production Wi-Fi envelope and delivery boundaries."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HeadTouchWifiTests(unittest.TestCase):
    def test_production_worker_order_retry_overflow_and_expiry(self):
        spec = importlib.util.spec_from_file_location("compiler_helper", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        self.assertIsNotNone(command)
        stubs = {
            "head_touch_wifi.local.h": '#pragma once\ninline constexpr char kTouchUrl[]="https://127.0.0.1/";\ninline constexpr char kTouchAuthorization[]="Bearer '+('a'*64)+'";\ninline constexpr char kTouchCa[]="test";\ninline constexpr char kTouchServerName[]="test";\n',
            "esp_log.h": '#pragma once\ninline void test_log(const char*, const char*, ...) {}\n#define ESP_LOGI test_log\n#define ESP_LOGW test_log\n',
            "esp_timer.h": '#pragma once\n#include <cstdint>\nint64_t esp_timer_get_time();\n',
            "freertos/FreeRTOS.h": '#pragma once\n#include <cstdint>\n#define pdTRUE 1\n#define pdPASS 1\n#define portMAX_DELAY 0xffffffff\n#define pdMS_TO_TICKS(x) (x)\nusing QueueHandle_t=void*;\nstruct StaticQueue_t{};\n',
            "freertos/queue.h": '#pragma once\n#include "FreeRTOS.h"\nQueueHandle_t xQueueCreateStatic(unsigned,unsigned,uint8_t*,StaticQueue_t*);\nint xQueueSend(QueueHandle_t,const void*,uint32_t);\nint xQueuePeek(QueueHandle_t,void*,uint32_t);\nint xQueueReceive(QueueHandle_t,void*,uint32_t);\n',
            "freertos/task.h": '#pragma once\n#include "FreeRTOS.h"\nvoid vTaskDelay(uint32_t);\nvoid vTaskDelete(void*);\nint xTaskCreate(void(*)(void*),const char*,unsigned,void*,unsigned,void*);\n',
            "esp_netif.h": '#pragma once\n#define ESP_OK 0\nstruct esp_netif_t{};\nstruct esp_netif_ip_info_t{struct{unsigned addr;}ip;};\nesp_netif_t* esp_netif_get_handle_from_ifkey(const char*);\nbool esp_netif_is_netif_up(esp_netif_t*);\nint esp_netif_get_ip_info(esp_netif_t*,esp_netif_ip_info_t*);\n',
            "esp_http_client.h": '#pragma once\n#define HTTP_METHOD_POST 1\nusing esp_http_client_handle_t=void*;\nstruct esp_http_client_config_t{const char* url;const char* cert_pem;const char* common_name;int method;int timeout_ms;int buffer_size;int buffer_size_tx;bool disable_auto_redirect;int max_authorization_retries;bool skip_cert_common_name_check;};\nesp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t*);\nint esp_http_client_set_header(esp_http_client_handle_t,const char*,const char*);\nint esp_http_client_set_post_field(esp_http_client_handle_t,const char*,int);\nint esp_http_client_perform(esp_http_client_handle_t);\nint esp_http_client_get_status_code(esp_http_client_handle_t);\nint esp_http_client_cleanup(esp_http_client_handle_t);\n',
        }
        with tempfile.TemporaryDirectory(prefix="touch-wifi-worker-", dir=ROOT / "tmp") as folder:
            folder = Path(folder)
            for path, text in stubs.items():
                target = folder / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            binary = folder / "worker.exe"
            args = command + (["c++"] if kind == "zig-c++" else []) + [
                "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I", str(folder), "-I", str(ROOT / "firmware/body/main/boards/esp_vocat"),
                str(ROOT / "tests/cpp/head_touch_wifi_worker_test.cc"), "-o", str(binary)]
            built = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)

    def test_production_envelope_and_retry_boundaries(self):
        spec = importlib.util.spec_from_file_location("compiler_helper", ROOT / "scripts/test-expression-e1-26-cpp.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        self.assertIsNotNone(command, "host C++ compiler required")
        with tempfile.TemporaryDirectory(prefix="touch-wifi-native-", dir=ROOT / "tmp") as folder:
            binary = Path(folder) / "touch-wifi.exe"
            args = command + (["c++"] if kind == "zig-c++" else []) + [
                "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-I", str(ROOT / "firmware/body/main/boards/esp_vocat"),
                str(ROOT / "tests/cpp/head_touch_wifi_test.cc"), "-o", str(binary)]
            built = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)


if __name__ == "__main__":
    unittest.main()
