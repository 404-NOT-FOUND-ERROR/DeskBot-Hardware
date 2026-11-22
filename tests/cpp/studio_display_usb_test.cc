// Exercise the retained USB task and snapshot implementation in one process.
#include <cassert>
#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <fcntl.h>
#include <unistd.h>

static bool changed = false, finished = false, flood = false, single = false;
static unsigned opens = 0, closes = 0, delays = 0, reads = 0, idle_ticks = 0;
static size_t offset = 0, chunk = 0;
static std::vector<std::string> incoming;

static int test_open(const char* path, int flags) {
    ++opens;
    assert(std::strcmp(path, "/dev/secondary") == 0 && flags == O_RDONLY);
    return 42;
}
static ssize_t test_read(int fd, void* data, size_t capacity) {
    assert(fd == 42 && capacity == 64 && ++reads == 1);
    if (flood) { std::memset(data, 'x', capacity); return capacity; }
    if (idle_ticks) { --idle_ticks; return 0; }
    if (chunk == incoming.size()) return 0;
    const auto& message = incoming[chunk];
    const size_t limit = single ? 1 : capacity;
    const size_t available = message.size() - offset;
    const size_t count = available < limit ? available : limit;
    std::memcpy(data, message.data() + offset, count);
    offset += count;
    if (offset == message.size()) { offset = 0; ++chunk; idle_ticks = 25; }
    return count;
}
static int test_close(int fd) { assert(fd == 42); ++closes; return 0; }
#define open test_open
#define read test_read
#define close test_close
#include "studio_usb_diagnostic.cc"
#undef open
#undef read
#undef close

void test_log(const char*, const char* format, ...) {
    char buffer[1100]; va_list args; va_start(args, format);
    std::vsnprintf(buffer, sizeof(buffer), format, args); va_end(args);
    std::puts(buffer);
}
int64_t esp_timer_get_time() { return 1000000 + delays * 10000; }
bool usb_serial_jtag_is_driver_installed() { return changed; }
bool usb_serial_jtag_is_connected() { return true; }
void vTaskDelete(void*) { finished = true; }
void vTaskDelay(unsigned ticks) {
    assert(ticks == 10 && reads == 1);
    reads = 0;
    ++delays;
    if (flood ? delays == 30 : chunk == incoming.size()) changed = true;
    assert(delays < 10000);
}
int xTaskCreate(void (*task)(void*), const char*, unsigned stack, void* context, unsigned priority, void*) {
    assert(stack == 6144 && priority == 1);
    task(context);
    return 1;
}

static std::string request(const char* operation, const char* id, bool opening = false) {
    return std::string("{\"schema\":\"deskbot.studio_session/v1\",\"operation\":\"") + operation +
        "\",\"request_id\":\"" + id + "\",\"target_id\":\"device-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\","
        "\"boot_id\":\"boot-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"client_nonce\":\"client-a\",\"session_id\":" +
        (opening ? "null" : "\"session-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1\"") + "}\n";
}

int main(int argc, char** argv) {
    assert(argc == 2);
    const std::string mode = argv[1];
    single = mode == "single";
    flood = mode == "flood";
    if (mode != "unknown") {
        { deskbot_studio_runtime::ApplicationTransition app(kDeviceStateIdle); }
        { deskbot_studio_runtime::PageTransition page;
          page.SetPage(mode == "menu" ? deskbot_studio_runtime::Page::Other : deskbot_studio_runtime::Page::Home); }
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
        deskbot_studio_runtime::ObservePlayer(mode == "stopping" ? deskbot_studio_display::Player::Stopping :
            deskbot_studio_display::Player::Initialized);
#endif
    }
    if (mode == "busy") {
        deskbot_studio_runtime::ApplicationTransition app(kDeviceStateSpeaking);
    }
    incoming = {request("open", "open", true)};
    if (mode == "no_session") incoming.clear();
    incoming.push_back(request("display", "display"));
    incoming.push_back(request("state", "state"));
    deskbot_studio_session::Start("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
    assert(finished && opens == 1 && closes == 1);
    std::printf("ticks=%u elapsed_ms=%u\n", delays, delays * 10);
}
