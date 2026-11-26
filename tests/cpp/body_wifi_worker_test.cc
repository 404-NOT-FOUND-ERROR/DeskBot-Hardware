#include "sdkconfig.h"
#include "studio_runtime_state.h"
#include "expression_emote/emote_assets.h"
#include "wifi_body_runtime.inc"
#include <cassert>
#include <deque>
#include <functional>
#include <iostream>
#include <string>
#include <vector>
#if __has_include("body_network_defaults.local.h")
#include "body_network_nvs_stub.h"
#endif

namespace fixture {
uint64_t now = 10000, stop = 22000;
std::deque<uint64_t> gestures;
std::deque<std::function<void()>> app;
void (*worker)(void*) = nullptr;
unsigned tasks = 0, active_http = 0, touch_attempts = 0, expression_attempts = 0, played = 0;
bool allocate = false, application = false, touch = false;
std::string posted;
std::vector<std::string> touches;
std::vector<char> order;
std::vector<std::string> urls;
esp_http_client_config_t config{};
deskbot_role::Details details{};
deskbot_studio_display::Snapshot display{};
struct Done {};
}
int64_t esp_timer_get_time() { return fixture::now * 1000; }
void vTaskDelete(void*) {}
void vTaskDelay(uint32_t ms) {
    fixture::now += ms;
    fixture::application = true;
    while (!fixture::app.empty()) { auto task = fixture::app.front(); fixture::app.pop_front(); task(); }
    fixture::application = false;
    if (fixture::now >= fixture::stop) throw fixture::Done{};
}
int xTaskCreate(void(*task)(void*), const char* name, unsigned stack, void*, unsigned priority, void*) {
    assert(!std::strcmp(name, "body_wifi") && stack == 6144 && priority == 1);
    ++fixture::tasks; fixture::worker = task;
    return fixture::allocate ? pdPASS : 0;
}
QueueHandle_t xQueueCreateStatic(unsigned count, unsigned size, uint8_t*, StaticQueue_t*) {
    assert(count == 8 && size == sizeof(uint64_t)); fixture::gestures.clear(); return &fixture::gestures;
}
int xQueueSend(QueueHandle_t, const void* item, uint32_t wait) {
    assert(wait == 0); if (fixture::gestures.size() == 8) return 0;
    fixture::gestures.push_back(*static_cast<const uint64_t*>(item)); return pdTRUE;
}
int xQueuePeek(QueueHandle_t, void* item, uint32_t) {
    if (fixture::gestures.empty()) return 0;
    *static_cast<uint64_t*>(item) = fixture::gestures.front(); return pdTRUE;
}
int xQueueReceive(QueueHandle_t q, void* item, uint32_t wait) {
    const int ok = xQueuePeek(q, item, wait); if (ok) fixture::gestures.pop_front(); return ok;
}
esp_netif_t* esp_netif_get_handle_from_ifkey(const char*) { static esp_netif_t sta; return &sta; }
bool esp_netif_is_netif_up(esp_netif_t*) { return true; }
int esp_netif_get_ip_info(esp_netif_t*, esp_netif_ip_info_t* ip) { ip->ip.addr = 1; return ESP_OK; }
Application& Application::GetInstance() { static Application app; return app; }
void Application::Schedule(std::function<void()> task) { assert(fixture::app.empty()); fixture::app.push_back(task); }
Board& Board::GetInstance() { assert(fixture::application); static Board board; return board; }
Display* Board::GetDisplay() { static emote::EmoteDisplay display; return &display; }
deskbot_role::Details deskbot_role::Describe(emote_handle_t) { assert(fixture::application); return fixture::details; }
bool deskbot_role::Replay(emote_handle_t, const char* semantic, esp_err_t& result) {
    assert(fixture::application && !std::strcmp(semantic, "happy")); ++fixture::played; result = ESP_OK; return true;
}
deskbot_studio_display::Snapshot deskbot_studio_runtime::ReadDisplay() { return fixture::display; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t* config) {
    assert(!fixture::application && fixture::active_http++ == 0);
    assert(config->timeout_ms == 3500 && config->disable_auto_redirect && !config->skip_cert_common_name_check);
    fixture::touch = std::strstr(config->url, "touch"); fixture::config = *config;
    fixture::urls.emplace_back(config->url);
    fixture::order.push_back(fixture::touch ? 'T' : 'E'); return reinterpret_cast<void*>(1);
}
int esp_http_client_set_header(esp_http_client_handle_t, const char*, const char*) { return ESP_OK; }
int esp_http_client_set_post_field(esp_http_client_handle_t, const char* data, int size) {
    fixture::posted.assign(data, size); return ESP_OK;
}
int esp_http_client_perform(esp_http_client_handle_t) {
    using namespace fixture;
#if __has_include("body_network_defaults.local.h")
    if (urls.size() == 1) {
        deskbot_body_network::Reply reply{};
        assert(deskbot_body_network::Handle("net-set:0:10.0.0.2:5318:5319", reply));
        assert(!std::strcmp(reply.result, "saved") && reply.config.revision == 1 && reply.applied_revision == 0);
        // A USB save during HTTP must not mutate this client's URL buffer.
        assert(!std::strcmp(config.url, "https://10.0.0.1:4318/api/hardware/head-touch"));
    }
#endif
    if (touch) { ++touch_attempts; touches.push_back(posted); now += touch_attempts == 1 ? 3500 : 20; }
    else {
        ++expression_attempts;
        if (expression_attempts == 1) {
            const auto query = now;
            const auto wire = "v1|cmd-" + std::string(24, 'a') + "|1|boot-" + std::string(32, 'c')
                + "|" + std::to_string(query) + "|3000|" + std::string(64, 'd') + "|round|3|happy\n";
            esp_http_client_event_t event{HTTP_EVENT_ON_DATA, static_cast<int>(wire.size()),
                const_cast<char*>(wire.data()), config.user_data};
            assert(config.event_handler(&event) == ESP_OK);
        }
        now += 20;
    }
    return ESP_OK;
}
int esp_http_client_get_status_code(esp_http_client_handle_t) {
    return fixture::touch ? (fixture::touch_attempts == 1 ? 503 : 204) : (fixture::expression_attempts == 1 ? 200 : 204);
}
int esp_http_client_cleanup(esp_http_client_handle_t) {
    assert(fixture::active_http-- == 1);
    assert(fixture::urls.back() == fixture::config.url);
    return ESP_OK;
}

int main() {
    using deskbot_body_wifi::Channel; using deskbot_body_wifi::Schedule;
    Schedule s;
    assert(s.Next(0) == Channel::Touch);
    s.CompleteTouch(3500, 1000); // Backoff starts when the slow request ends.
    assert(s.Next(3500) == Channel::Expression);
    s.CompleteExpression(3520, true);
    assert(s.Next(4499) == Channel::None && s.Next(4500) == Channel::Touch);
    s.CompleteTouch(4500, 0);
    assert(s.Next(5020) == Channel::Expression); // Gesture backlog cannot starve expressions.
    s.CompleteExpression(5020, false);
    s.CompleteTouch(5100, 250);
    assert(s.Next(5350) == Channel::Touch && s.Next(8020) == Channel::Expression);
    s.CompleteExpression(8020, true);
    s.CompleteTouch(8020, 30000);
    assert(s.Next(9519) == Channel::None && s.Next(9520) == Channel::Expression);
    assert(s.SleepMs(8020) == 250 && s.SleepMs(9520) == 10);
    Schedule capped;
    for (unsigned i = 0; i < 20; ++i) capped.CompleteExpression(0, false);
    capped.CompleteTouch(0, 31000);
    assert(capped.Next(29999) == Channel::None && capped.Next(30000) == Channel::Expression);

    const std::string device(32, 'a'), boot(32, 'c'), firmware(64, 'b');
    deskbot_body_wifi::Start("bad", boot.c_str(), firmware.c_str()); assert(fixture::tasks == 0);
    deskbot_body_wifi::Start(device.c_str(), boot.c_str(), firmware.c_str()); assert(fixture::tasks == 1);
    deskbot_head_touch_wifi::Observe(10000); assert(fixture::gestures.empty()); // Failed allocation disables enqueue.
    fixture::allocate = true;
    deskbot_body_wifi::Start(device.c_str(), boot.c_str(), firmware.c_str());
    deskbot_body_wifi::Start(device.c_str(), boot.c_str(), firmware.c_str()); assert(fixture::tasks == 2);
    fixture::details.loaded = true; fixture::details.count = 1; fixture::details.target = 0;
    fixture::details.phase = deskbot_role::Phase::Loop;
    std::memset(fixture::details.graph_sha256, 'd', 64); std::strcpy(fixture::details.roles[0], "round");
    fixture::details.selection = 3; deskbot_wifi_expression::Observe(fixture::details);
    fixture::display.player.value = deskbot_studio_display::Player::Initialized;
    fixture::display.runtime.application = {1, kDeviceStateIdle, deskbot_studio_runtime::Phase::Settled};
    fixture::display.runtime.page = {1, static_cast<uint8_t>(deskbot_studio_runtime::Page::Home), deskbot_studio_runtime::Phase::Settled};
    for (unsigned i = 0; i < 9; ++i) deskbot_head_touch_wifi::Observe(9992 + i);
    try { fixture::worker(nullptr); } catch (fixture::Done&) {}
    assert(fixture::gestures.empty() && fixture::active_http == 0);
    assert(fixture::order[0] == 'T' && fixture::order[1] == 'E');
    std::cerr << "touch=" << fixture::touch_attempts << " expression=" << fixture::expression_attempts
              << " played=" << fixture::played << "\n";
    assert(fixture::touch_attempts == 9 && fixture::expression_attempts >= 3 && fixture::played == 1);
    assert(fixture::touches[0].find("\"uptime_ms\":9992,") != std::string::npos);
    assert(fixture::touches[1].find("\"uptime_ms\":9992,") != std::string::npos);
    assert(fixture::touches.back().find("\"uptime_ms\":9999,") != std::string::npos);
    assert(deskbot_wifi_expression::control.Available());
    assert(deskbot_head_touch_wifi::Poll() == 250);
    deskbot_head_touch_wifi::Observe(fixture::now + 1); assert(deskbot_head_touch_wifi::Poll() == 0);
    assert(fixture::touch_attempts == 9); // Future/stale gestures are removed without HTTP.
#if __has_include("body_network_defaults.local.h")
    assert(fixture::urls[0] == "https://10.0.0.1:4318/api/hardware/head-touch");
    bool new_touch = false, new_expression = false;
    for (unsigned i = 1; i < fixture::urls.size(); ++i) {
        const auto& url = fixture::urls[i];
        if (url == "https://10.0.0.2:5318/api/hardware/head-touch") new_touch = true;
        else { assert(url == "https://10.0.0.2:5319/api/hardware/expression/exchange"); new_expression = true; }
    }
    assert(new_touch && new_expression && network_fixture::writes == 1);
    // Reinitialization models a new boot; both URLs come from the same saved record.
    assert(deskbot_body_network::Initialize("10.0.0.1", 4318, 4319));
    assert(deskbot_body_network::ApplyUrls());
    assert(!std::strcmp(deskbot_body_network::TouchUrl(), "https://10.0.0.2:5318/api/hardware/head-touch"));
    assert(!std::strcmp(deskbot_body_network::ExpressionUrl(), "https://10.0.0.2:5319/api/hardware/expression/exchange"));
#endif
    std::cout << "shared Wi-Fi worker passed\n";
}
