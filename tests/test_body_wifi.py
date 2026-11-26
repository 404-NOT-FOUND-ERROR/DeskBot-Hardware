"""Run the actual shared worker with queued gestures and serialized HTTP clients."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.test_wifi_expression import compile_native, worker_stubs, ROOT
from tests.test_body_network_settings import NVS_STUB
from tests.test_body_credentials import compile_credentials, credential_stubs


class BodyWifiTests(unittest.TestCase):
    def test_shared_worker_and_schedule(self):
        self.run_worker(False)

    def test_saved_address_switches_between_requests_and_survives_reload(self):
        self.run_worker(True)

    def run_worker(self, settings):
        stubs = worker_stubs()
        if settings:
            stubs['nvs.h'] = NVS_STUB
            stubs.update(credential_stubs())
            stubs['body_network_defaults.local.h'] = ('#pragma once\n'
                'inline constexpr char kBodyNetworkHost[]="10.0.0.1";\n'
                'inline constexpr uint16_t kBodyTouchPort=4318,kBodyExpressionPort=4319;\n'
                'inline constexpr char kBodyBuiltinCredentials[]="' + '6' * 32 + '";\n')
            stubs['esp_http_client.h'] = stubs['esp_http_client.h'].replace(
                '#define HTTP_METHOD_POST 1', '#define HTTP_METHOD_POST 1\n#define HTTP_METHOD_HEAD 3').replace(
                'const char* cert_pem;', 'const char* cert_pem;size_t cert_len;')
        stubs["head_touch_wifi.local.h"] = stubs["wifi_expression.local.h"].replace("kExpression", "kTouch").replace(
            'https://127.0.0.1/', 'https://127.0.0.1/touch')
        stubs["freertos/FreeRTOS.h"] += ('#define pdTRUE 1\n#define portMAX_DELAY 0xffffffff\n'
            'using QueueHandle_t=void*;\nstruct StaticQueue_t{};\n')
        stubs["freertos/queue.h"] = ('#pragma once\n#include "FreeRTOS.h"\n'
            'QueueHandle_t xQueueCreateStatic(unsigned,unsigned,uint8_t*,StaticQueue_t*);\n'
            'int xQueueSend(QueueHandle_t,const void*,uint32_t);\n'
            'int xQueuePeek(QueueHandle_t,void*,uint32_t);\n'
            'int xQueueReceive(QueueHandle_t,void*,uint32_t);\n')
        stubs["freertos/task.h"] += 'void vTaskDelete(void*);\n'
        with tempfile.TemporaryDirectory(prefix="body-wifi-", dir=ROOT / "tmp") as folder:
            folder = Path(folder)
            for name, text in stubs.items():
                target = folder / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            binary = folder / "combined.exe"
            compile = compile_credentials if settings else compile_native
            compile(ROOT / "tests/cpp/body_wifi_worker_test.cc", binary, (folder, ROOT / "tests/cpp",
                    ROOT / "tests/cpp/layout_stubs", ROOT / "tests/cpp/catalog_stubs", ROOT / "firmware/body/main"))
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
