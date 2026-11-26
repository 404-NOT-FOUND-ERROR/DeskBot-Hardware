#include "head_touch_wifi.h"

#if __has_include("head_touch_wifi.local.h")
#include "head_touch_wifi.local.h"
#if __has_include("body_network_defaults.local.h")
#include "body_network_config.h"
#endif
#if __has_include("body_network_defaults.local.h") && __has_include("wifi_expression.local.h")
#include "body_network_credentials.h"
#endif

#include <atomic>
#include <cstring>
#include <ctime>
#include <esp_http_client.h>
#include <esp_log.h>
#include <esp_netif.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

namespace deskbot_head_touch_wifi {
namespace {
constexpr const char* kTag = "DeskBotTouchWifi";
static_assert(sizeof(kTouchUrl) > sizeof("https://") && kTouchUrl[0] == 'h'
              && kTouchUrl[4] == 's' && sizeof(kTouchAuthorization) == 72);
std::atomic<QueueHandle_t> touch_queue{nullptr};
std::atomic<uint32_t> overflow{0};
StaticQueue_t queue_control;
alignas(uint64_t) uint8_t queue_storage[kQueueLength * sizeof(uint64_t)];
char device_hex[33], boot_hex[33], firmware_hex[65];
unsigned retry_attempt = 0;

uint64_t Uptime() {
    return static_cast<uint64_t>(esp_timer_get_time() / 1000);
}

bool Ready() {
    auto* station = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_ip_info_t ip{};
    // Wi-Fi and the official OTA clock are already owned by the existing app.
    return station && esp_netif_is_netif_up(station) &&
           esp_netif_get_ip_info(station, &ip) == ESP_OK && ip.ip.addr &&
           std::time(nullptr) >= 1700000000;
}

int Send(uint64_t event_ms) {
    char body[288];
    const int size = Encode(body, sizeof(body), device_hex, boot_hex, firmware_hex, event_ms, Uptime());
    if (!size) return 400;
    esp_http_client_config_t config{};
#if __has_include("body_network_defaults.local.h")
    config.url = deskbot_body_network::TouchUrl();
#else
    config.url = kTouchUrl;
#endif
    config.cert_pem = kTouchCa;
    config.common_name = kTouchServerName;
    const char* authorization = kTouchAuthorization;
#if __has_include("body_network_defaults.local.h") && __has_include("wifi_expression.local.h")
    const auto credentials = deskbot_body_credentials::Active();
    if (credentials) {
        config.cert_pem = credentials->Certificate(deskbot_body_credentials::Channel::Touch);
        config.cert_len = credentials->cert_size[0];
        authorization = credentials->authorization[0];
    }
#endif
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = 3500;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.disable_auto_redirect = true;
    config.max_authorization_retries = -1;
    auto client = esp_http_client_init(&config);
    if (!client) return 0;
    int status = 0;
    if (esp_http_client_set_header(client, "Content-Type", "application/json") == ESP_OK &&
        esp_http_client_set_header(client, "Authorization", authorization) == ESP_OK &&
        esp_http_client_set_post_field(client, body, size) == ESP_OK &&
        esp_http_client_perform(client) == ESP_OK) {
        status = esp_http_client_get_status_code(client);
    }
    esp_http_client_cleanup(client);
    return status;
}

void Run(void* argument) {
    auto queue = static_cast<QueueHandle_t>(argument);
    uint64_t gesture = 0;
    while (xQueuePeek(queue, &gesture, portMAX_DELAY) == pdTRUE) {
        const auto delay = Poll();
        if (delay) vTaskDelay(pdMS_TO_TICKS(delay));
    }
    vTaskDelete(nullptr);
}
}  // namespace

bool Prepare(const char* device, const char* boot, const char* firmware) {
    if (!deskbot_studio_link::Hex(device, 32) || !deskbot_studio_link::Hex(boot, 32)
        || !deskbot_studio_link::Hex(firmware, 64)) return false;
    std::memcpy(device_hex, device, sizeof(device_hex));
    std::memcpy(boot_hex, boot, sizeof(boot_hex));
    std::memcpy(firmware_hex, firmware, sizeof(firmware_hex));
    auto queue = xQueueCreateStatic(kQueueLength, sizeof(uint64_t), queue_storage, &queue_control);
    if (!queue) return false;
    retry_attempt = 0;
    touch_queue.store(queue, std::memory_order_release);
    return true;
}

uint32_t Poll() {
    auto queue = touch_queue.load(std::memory_order_acquire);
    uint64_t gesture = 0;
    if (!queue || xQueuePeek(queue, &gesture, 0) != pdTRUE) return 250;
    const auto lost = overflow.exchange(0, std::memory_order_relaxed);
    if (lost) ESP_LOGW(kTag, "queue full, discarded=%u", static_cast<unsigned>(lost));
    if (!Fresh(gesture, Uptime())) {
        ESP_LOGW(kTag, "expired");
    } else {
        const int status = Ready() ? Send(gesture) : 0;
#if __has_include("body_network_defaults.local.h") && __has_include("wifi_expression.local.h")
        const auto result = Classify(status, true);
#else
        const auto result = Classify(status);
#endif
        if (result == Delivery::Retry) {
            const auto delay = RetryDelayMs(retry_attempt);
            if (retry_attempt < 5) ++retry_attempt;
            return delay;
        }
        ESP_LOGI(kTag, "delivery=%s status=%d", result == Delivery::Recorded ? "recorded" : "rejected", status);
    }
    // The sole network worker removes events. A retry keeps the original head.
    xQueueReceive(queue, &gesture, 0);
    retry_attempt = 0;
    return 0;
}

void Disable() { touch_queue.store(nullptr, std::memory_order_release); }

void Start(const char* device, const char* boot, const char* firmware) {
    if (!Prepare(device, boot, firmware)) return;
    auto queue = touch_queue.load(std::memory_order_acquire);
    if (xTaskCreate(Run, "touch_wifi", 6144, queue, 1, nullptr) == pdPASS) {
        ESP_LOGI(kTag, "ready");
    } else {
        Disable();
        ESP_LOGW(kTag, "task allocation unavailable");
    }
}

void Observe(uint64_t event_ms) {
    const auto queue = touch_queue.load(std::memory_order_acquire);
    if (queue && xQueueSend(queue, &event_ms, 0) != pdTRUE) overflow.fetch_add(1, std::memory_order_relaxed);
}
}  // namespace deskbot_head_touch_wifi
#endif
