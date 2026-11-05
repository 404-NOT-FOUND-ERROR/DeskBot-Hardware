#include "sdkconfig.h"

#if CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE
#include "studio_usb_diagnostic.h"
#include "studio_usb_session.h"
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
#include "studio_expression_adapter.h"
#endif
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
#include "studio_runtime_state.h"
#endif
#include <cerrno>
#include <driver/usb_serial_jtag.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <fcntl.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <unistd.h>

namespace deskbot_studio_session {
namespace {
Endpoint endpoint;
#if CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
constexpr char kUsbDevice[] = "/dev/secondary";
#elif CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
constexpr char kUsbDevice[] = "/dev/usbserjtag";
#else
#error "Studio USB input requires a USB Serial/JTAG console"
#endif
void DiagnosticTask(void*) {
    // The basic VFS always reads without blocking. O_RDONLY preserves its
    // default shared flag. No global mode or console function is changed.
    const int fd = open(kUsbDevice, O_RDONLY);
    if (fd < 0) {
        ESP_LOGW("DeskBotSession", "USB input unavailable (%d)", errno);
        vTaskDelete(nullptr);
        return;
    }
    ESP_LOGI("DeskBotSession", "USB input ready: %s", kUsbDevice);
    bool connected = false;
    uint8_t input[64];
    char reply[1024];
    for (;;) {
        if (usb_serial_jtag_is_driver_installed()) break;
        const auto now = static_cast<uint64_t>(esp_timer_get_time() / 1000);
        const bool online = usb_serial_jtag_is_connected();
        if (!online) {
            if (connected) endpoint.Disconnect();
        } else {
            endpoint.Tick(now);
            // IDF's basic VFS can return one byte even with a larger buffer.
            // Drain a bounded batch before yielding, including under flood.
            for (size_t read_count = 0, received = 0;
                 read_count < 512 && received < 512; ++read_count) {
                const size_t room = 512 - received;
                const auto count = read(fd, input, room < sizeof(input) ? room : sizeof(input));
                if (count <= 0) break;
                received += static_cast<size_t>(count);
                for (ssize_t i = 0; i < count; ++i) {
                    if (endpoint.Feed(input[i], now, reply, sizeof(reply))) {
                        ESP_LOGI("DeskBotSession", "%s", reply);
                    }
                }
            }
        }
        connected = online;
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
        if (deskbot_studio_expression::Poll(reply, sizeof(reply)))
            ESP_LOGI("DeskBotSession", "%s", reply);
#endif
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    endpoint.Disconnect();
    close(fd);
    ESP_LOGW("DeskBotSession", "USB input owner changed");
    vTaskDelete(nullptr);
}
}  // namespace

void Start(const char* device_hex, const char* boot_hex) {
    if (usb_serial_jtag_is_driver_installed() || !endpoint.Initialize(device_hex, boot_hex)) {
        ESP_LOGW("DeskBotSession", "USB input configuration unavailable");
        return;
    }
#if CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE
    endpoint.SetStateProvider(deskbot_studio_runtime::Read);
#endif
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
    endpoint.SetDisplayProvider(deskbot_studio_runtime::ReadDisplay);
#endif
#if CONFIG_DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE
    endpoint.SetExpressionHooks(deskbot_studio_expression::AdapterHooks());
#endif
    if (xTaskCreate(DiagnosticTask, "studio_session", 6144, nullptr, 1, nullptr) != pdPASS) {
        ESP_LOGW("DeskBotSession", "session task allocation unavailable");
    }
}
}  // namespace deskbot_studio_session
#endif
