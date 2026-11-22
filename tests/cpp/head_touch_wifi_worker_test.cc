#include <cassert>
#include <cstring>
#include <deque>
#include <iostream>
#include <string>
#include <vector>

#include "head_touch_wifi.inc"

namespace fixture {
uint64_t now = 100000;
std::deque<uint64_t> queue;
void (*task)(void*) = nullptr;
void* argument = nullptr;
std::deque<int> statuses;
std::vector<std::string> sent;
std::string content;
bool create_task = true;
int64_t delay_total = 0;
}  // namespace fixture

int64_t esp_timer_get_time() { return static_cast<int64_t>(fixture::now * 1000); }
void vTaskDelay(uint32_t ms) { fixture::now += ms; fixture::delay_total += ms; }
void vTaskDelete(void*) {}
QueueHandle_t xQueueCreateStatic(unsigned, unsigned, uint8_t*, StaticQueue_t*) { return &fixture::queue; }
int xQueueSend(QueueHandle_t, const void* item, uint32_t) {
    if (fixture::queue.size() == 8) return 0;
    fixture::queue.push_back(*static_cast<const uint64_t*>(item));
    return pdTRUE;
}
int xQueuePeek(QueueHandle_t, void* item, uint32_t) {
    if (fixture::queue.empty()) return 0;
    *static_cast<uint64_t*>(item) = fixture::queue.front();
    return pdTRUE;
}
int xQueueReceive(QueueHandle_t, void* item, uint32_t) {
    const int result = xQueuePeek(nullptr, item, 0);
    if (result) fixture::queue.pop_front();
    return result;
}
int xTaskCreate(void (*task)(void*), const char*, unsigned size, void* arg, unsigned, void*) {
    assert(size == 6144);
    fixture::task = task;
    fixture::argument = arg;
    return fixture::create_task ? pdPASS : 0;
}
esp_netif_t* esp_netif_get_handle_from_ifkey(const char*) { static esp_netif_t result; return &result; }
bool esp_netif_is_netif_up(esp_netif_t*) { return true; }
int esp_netif_get_ip_info(esp_netif_t*, esp_netif_ip_info_t* ip) { ip->ip.addr = 1; return ESP_OK; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t* config) {
    assert(config->timeout_ms == 3500 && config->disable_auto_redirect && config->cert_pem);
    assert(!config->skip_cert_common_name_check && std::strstr(config->url, "https://") == config->url);
    return reinterpret_cast<void*>(1);
}
int esp_http_client_set_header(esp_http_client_handle_t, const char*, const char*) { return ESP_OK; }
int esp_http_client_set_post_field(esp_http_client_handle_t, const char* body, int size) {
    fixture::content.assign(body, size); return ESP_OK;
}
int esp_http_client_perform(esp_http_client_handle_t) {
    fixture::sent.push_back(fixture::content);
    return ESP_OK;
}
int esp_http_client_get_status_code(esp_http_client_handle_t) {
    if (fixture::statuses.empty()) return 204;
    const int status = fixture::statuses.front(); fixture::statuses.pop_front(); return status;
}
int esp_http_client_cleanup(esp_http_client_handle_t) { return ESP_OK; }

int main() {
    using namespace deskbot_head_touch_wifi;
    char device[33], boot[33], firmware[65];
    std::memset(device, 'a', 32); device[32] = 0;
    std::memset(boot, 'b', 32); boot[32] = 0;
    std::memset(firmware, 'c', 64); firmware[64] = 0;
    Start(device, boot, firmware);
    for (unsigned i = 0; i < 9; ++i) Observe(fixture::now + i);
    fixture::statuses = {503, 503, 204};
    fixture::task(fixture::argument);
    assert(fixture::sent.size() == 10 && fixture::delay_total == 3000);
    for (unsigned i = 0; i < 3; ++i) assert(fixture::sent[i].find("\"uptime_ms\":100000,") != std::string::npos);
    assert(fixture::sent.back().find("\"uptime_ms\":100007,") != std::string::npos);
    assert(fixture::queue.empty());
    fixture::sent.clear();
    Observe(fixture::now);
    fixture::now += 300000;
    fixture::task(fixture::argument);
    assert(fixture::sent.empty());  // Stale events expire without an HTTP request.
    Observe(fixture::now);
    Observe(fixture::now);
    fixture::statuses = {401, 204};
    fixture::task(fixture::argument);
    assert(fixture::sent.size() == 2 && fixture::queue.empty());
    std::cout << "head-touch-wifi worker passed\n";
}
