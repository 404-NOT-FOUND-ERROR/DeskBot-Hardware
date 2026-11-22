#include "sdkconfig.h"
#include "studio_runtime_state.h"
#include "wifi_expression.inc"
#include <cassert>
#include <deque>
#include <functional>
#include <iostream>
#include <string>
#include <thread>

namespace fixture {
uint64_t now = 10000;
bool application = false, network = true, has_display = true, submit = true;
int http_status = 200, transport_result = 0;
uint64_t latency = 10;
std::string response, posted;
std::deque<std::function<void()>> queue;
esp_http_client_config_t config{};
deskbot_role::Details details{};
deskbot_studio_display::Snapshot display{};
unsigned submitted = 0, reads = 0;
std::string expression;
void Unlocked() {
    bool acquired = false;
    std::thread other([&] { acquired = deskbot_wifi_expression::mutex.try_lock();
        if (acquired) deskbot_wifi_expression::mutex.unlock(); });
    other.join(); assert(acquired);
}
void Init() {
    using namespace deskbot_wifi_expression;
    now = 10000; control = {}; queue.clear(); usb_deadline = 0;
    application = false; network = has_display = submit = true;
    http_status = 200; transport_result = 0; latency = 10; submitted = 0;
    response.clear(); posted.clear();
    details = {}; details.loaded = true; details.count = 1; details.target = 0;
    std::memset(details.graph_sha256, 'd', 64); std::strcpy(details.roles[0], "round");
    details.selection = 3; details.phase = deskbot_role::Phase::Loop;
    display = {}; display.player.value = deskbot_studio_display::Player::Initialized;
    display.runtime.application = {1, kDeviceStateIdle, deskbot_studio_runtime::Phase::Settled};
    display.runtime.page = {1, static_cast<uint8_t>(deskbot_studio_runtime::Page::Home), deskbot_studio_runtime::Phase::Settled};
    Observe(details);
}
std::string Wire(uint64_t sequence = 1) {
    return "v1|cmd-" + std::string(24, 'a') + "|" + std::to_string(sequence) + "|boot-" + std::string(32, 'c')
        + "|" + std::to_string(now) + "|3000|" + std::string(64, 'd') + "|round|3|happy\n";
}
void RunQueued() {
    application = true;
    while (!queue.empty()) { auto task = queue.front(); queue.pop_front(); task(); }
    application = false;
}
}  // namespace fixture

Application& Application::GetInstance() { static Application app; return app; }
void Application::Schedule(std::function<void()> task) {
    fixture::Unlocked(); assert(fixture::queue.empty()); fixture::queue.push_back(task);
}
Board& Board::GetInstance() { assert(fixture::application); static Board board; return board; }
Display* Board::GetDisplay() {
    assert(fixture::application); fixture::Unlocked(); static emote::EmoteDisplay display;
    return fixture::has_display ? &display : nullptr;
}
deskbot_role::Details deskbot_role::Describe(emote_handle_t handle) {
    assert(fixture::application); fixture::Unlocked(); ++fixture::reads;
    return handle ? fixture::details : Details{};
}
bool deskbot_role::Replay(emote_handle_t handle, const char* semantic, esp_err_t& result) {
    assert(fixture::application && handle); fixture::Unlocked(); ++fixture::submitted;
    fixture::expression = semantic; result = fixture::submit ? ESP_OK : ESP_FAIL; return true;
}
deskbot_studio_display::Snapshot deskbot_studio_runtime::ReadDisplay() { fixture::Unlocked(); return fixture::display; }
int64_t esp_timer_get_time() { return static_cast<int64_t>(fixture::now * 1000); }
esp_netif_t* esp_netif_get_handle_from_ifkey(const char*) { static esp_netif_t station; return &station; }
bool esp_netif_is_netif_up(esp_netif_t*) { return fixture::network; }
int esp_netif_get_ip_info(esp_netif_t*, esp_netif_ip_info_t* ip) { ip->ip.addr = 1; return ESP_OK; }
void vTaskDelay(uint32_t) {}
int xTaskCreate(void(*)(void*), const char*, unsigned stack, void*, unsigned, void*) { assert(stack == 6144); return pdPASS; }
esp_http_client_handle_t esp_http_client_init(const esp_http_client_config_t* config) {
    fixture::Unlocked(); assert(config->cert_pem && config->common_name && config->disable_auto_redirect);
    assert(!config->skip_cert_common_name_check && config->timeout_ms == 3500);
    fixture::config = *config; return reinterpret_cast<void*>(1);
}
int esp_http_client_set_header(esp_http_client_handle_t, const char*, const char*) { return ESP_OK; }
int esp_http_client_set_post_field(esp_http_client_handle_t, const char* body, int size) { fixture::posted.assign(body, size); return ESP_OK; }
int esp_http_client_perform(esp_http_client_handle_t) {
    fixture::Unlocked(); fixture::now += fixture::latency;
    for (size_t i = 0; i < fixture::response.size(); i += 7) {
        const auto part = fixture::response.substr(i, 7);
        esp_http_client_event_t event{HTTP_EVENT_ON_DATA, static_cast<int>(part.size()), const_cast<char*>(part.data()), fixture::config.user_data};
        if (fixture::config.event_handler(&event) != ESP_OK) return ESP_FAIL;
    }
    return fixture::transport_result;
}
int esp_http_client_get_status_code(esp_http_client_handle_t) { return fixture::http_status; }
int esp_http_client_cleanup(esp_http_client_handle_t) { return ESP_OK; }

int main() {
    using namespace deskbot_wifi_expression;
    const std::string device(32, 'a'), boot(32, 'c'), firmware(64, 'b');
    Start(device.c_str(), boot.c_str(), firmware.c_str());
    fixture::Init(); fixture::response = fixture::Wire();
    assert(PollOnce() && fixture::queue.size() == 1 && fixture::submitted == 0 && fixture::reads == 0);
    fixture::RunQueued(); assert(fixture::submitted == 1 && fixture::expression == "happy");
    fixture::http_status = 503; assert(!PollOnce());
    assert(std::strstr(fixture::posted.c_str(), "submitted_to_player"));
    assert(control.ReadReceipt().result == Result::Dispatched);
    // Retry a successful execution with a fresh query timestamp: no replay.
    fixture::http_status = 200; fixture::response = fixture::Wire();
    assert(PollOnce() && fixture::queue.empty() && fixture::submitted == 1);
    fixture::http_status = 204; fixture::response.clear(); assert(PollOnce() && control.Available());
    for (unsigned scenario = 0; scenario < 6; ++scenario) {
        fixture::Init(); fixture::response = fixture::Wire(); assert(PollOnce());
        if (scenario == 0) fixture::display.runtime.page.value = static_cast<uint8_t>(deskbot_studio_runtime::Page::Other);
        if (scenario == 1) fixture::display.runtime.application.value = kDeviceStateSpeaking;
        if (scenario == 2) ++fixture::details.selection;
        if (scenario == 3) UsbLease(true, fixture::now + 1000);
        if (scenario == 4) fixture::now += 3000;
        if (scenario == 5) fixture::submit = false;
        fixture::RunQueued();
        const Result expected[] = {Result::OwnerBusy, Result::OwnerBusy, Result::BindingChanged, Result::UsbBusy, Result::Expired, Result::PlayerUnavailable};
        assert(control.ReadReceipt().result == expected[scenario]);
        assert(fixture::submitted == (scenario == 5 ? 1u : 0u));
    }
    // A command can expire while its callback waits. A later callback carries
    // its old ticket, so it cannot play a new command or play twice.
    fixture::Init(); fixture::response = fixture::Wire(); assert(PollOnce());
    fixture::now += 3000; fixture::http_status = 204; fixture::response.clear(); assert(PollOnce());
    assert(std::strstr(fixture::posted.c_str(), "\"status\":\"expired\""));
    fixture::RunQueued(); assert(fixture::submitted == 0);
    fixture::Init(); fixture::latency = 3000; fixture::response = fixture::Wire();
    assert(PollOnce() && fixture::queue.empty() && control.ReadReceipt().result == Result::Expired);
    fixture::Init(); fixture::response.assign(kReplyBytes + 1, 'a'); assert(!PollOnce() && fixture::queue.empty());
    fixture::Init(); fixture::network = false; assert(!PollOnce() && fixture::posted.empty());
    fixture::Init(); fixture::details.asleep = true; Observe(fixture::details);
    fixture::http_status = 204; assert(PollOnce() && fixture::posted.find("\"ready\":false") != std::string::npos);
    std::cout << "wifi expression worker passed\n";
}
