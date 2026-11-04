#include "sdkconfig.h"

#if CONFIG_DESKBOT_STUDIO_IDENTITY_ENABLE
#include "studio_device_link.h"
#if CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE
#include "studio_usb_diagnostic.h"
#endif
#include <esp_app_desc.h>
#include <esp_flash.h>
#include <esp_log.h>
#include <esp_mac.h>
#include <esp_psram.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mbedtls/sha256.h>

namespace deskbot_studio_link {
namespace {
void ToHex(const uint8_t* bytes, size_t count, char* result) {
    constexpr char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < count; ++i) {
        result[2 * i] = digits[bytes[i] >> 4];
        result[2 * i + 1] = digits[bytes[i] & 15];
    }
    result[2 * count] = '\0';
}

void IdentityTask(void*) {
    // Stable public-facing ID, separate from the MAC used by the cloud stack.
    uint8_t identity_seed[22] = {'d','e','s','k','b','o','t','-','l','i','n','k','-','v','1',0};
    uint8_t digest[32], nonce[16];
    char device_hex[33], boot_hex[33], app_hash[65];
    if (esp_read_mac(identity_seed + 16, ESP_MAC_WIFI_STA) != ESP_OK ||
        mbedtls_sha256(identity_seed, sizeof(identity_seed), digest, 0) != 0) {
        ESP_LOGW("DeskBotLink", "identity unavailable");
        vTaskDelete(nullptr);
        return;
    }
    ToHex(digest, 16, device_hex);
    esp_fill_random(nonce, sizeof(nonce));
    ToHex(nonce, sizeof(nonce), boot_hex);
#if CONFIG_DESKBOT_STUDIO_USB_DIAGNOSTIC_ENABLE
    deskbot_studio_session::Start(device_hex, boot_hex);
#endif
    const auto* app = esp_app_get_description();
    ToHex(app->app_elf_sha256, 32, app_hash);
    uint32_t flash_bytes = 0;
    esp_flash_get_size(nullptr, &flash_bytes);
    const uint32_t psram_bytes = static_cast<uint32_t>(esp_psram_get_size());
    char message[1024];
    uint32_t sequence = 0;
    for (;;) {
        if (BuildHello(message, sizeof(message), device_hex, boot_hex, app_hash,
                       app->version, sequence, static_cast<uint64_t>(esp_timer_get_time() / 1000),
                       flash_bytes, psram_bytes)) {
            ESP_LOGI("DeskBotLink", "%s", message);
        }
        // No input, UI access, application mutex, network, NVS or base access.
        if (sequence == UINT32_MAX) break;
        ++sequence;
        vTaskDelay(pdMS_TO_TICKS(kHelloIntervalMs));
    }
    vTaskDelete(nullptr);
}
}  // namespace

void Start() {
    if (xTaskCreate(IdentityTask, "studio_identity", 4096, nullptr, 1, nullptr) != pdPASS) {
        ESP_LOGW("DeskBotLink", "identity task allocation unavailable");
    }
}
}  // namespace deskbot_studio_link
#endif
