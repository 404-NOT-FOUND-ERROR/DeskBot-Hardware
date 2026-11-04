#include "studio_usb_session.h"
#include "studio_runtime_state.h"
#include <atomic>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <thread>

static_assert(sizeof(deskbot_studio_session::Endpoint) <= 3072, "bounded display endpoint");
static deskbot_studio_display::Snapshot current;
static unsigned reads = 0;
static deskbot_studio_display::Snapshot Read() { ++reads; return current; }

static int AdapterTest() {
    using namespace deskbot_studio_runtime;
    using deskbot_studio_display::Player;
    deskbot_studio_display::Store exhausted(UINT32_MAX - 1);
    exhausted.Observe(Player::Initialized);
    exhausted.Observe(Player::Initialized);
    if (exhausted.Read().value != Player::Unknown || exhausted.Read().revision != UINT32_MAX) return 10;
    if (ReadDisplay().player.revision != 0) return 11;
    { ApplicationTransition transition(kDeviceStateIdle); }
    { PageTransition transition; transition.SetPage(Page::Home); }
    ObservePlayer(Player::Initialized);
    const auto snapshot = ReadDisplay();
    if (std::strcmp(deskbot_studio_display::Reason(snapshot), "sleep_unknown")) return 12;
    std::atomic<bool> failed{false};
    std::thread player([] { for (int i = 0; i < 20000; ++i) ObservePlayer(i % 2 ? Player::Initialized : Player::Stopping); });
    std::thread app([] { for (int i = 0; i < 20000; ++i) { ApplicationTransition transition(i % 2 ? kDeviceStateIdle : kDeviceStateSpeaking); } });
    std::thread reader([&failed] {
        uint32_t revision = 0;
        for (int i = 0; i < 50000; ++i) {
            const auto s = ReadDisplay();
            if (s.player.revision < revision || s.runtime.controls_available ||
                s.runtime.sleep != Sleep::Unknown) failed = true;
            revision = s.player.revision;
        }
    });
    player.join(); app.join(); reader.join();
    return failed ? 13 : 0;
}

int main(int argc, char** argv) {
    if (argc != 2) return 1;
    if (!std::strcmp(argv[1], "adapter")) return AdapterTest();
    std::ifstream input(argv[1], std::ios::binary);
    const std::string raw((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    deskbot_studio_session::Endpoint endpoint;
    const std::string device(32, 'a'), boot(32, 'b');
    if (!endpoint.Initialize(device.c_str(), boot.c_str())) return 2;
    endpoint.SetDisplayProvider(Read);
    size_t position = 0, capacity = 1024;
    const auto number = [&raw, &position](size_t bytes) {
        uint64_t value = 0;
        for (size_t i = 0; i < bytes; ++i)
            value |= static_cast<uint64_t>(static_cast<uint8_t>(raw[position++])) << (8 * i);
        return value;
    };
    char reply[1025]{};
    reply[1024] = 'z';
    while (position < raw.size()) {
        const char op = raw[position++];
        if (op == 'X') { endpoint.Disconnect(); continue; }
        if (op == 'Q') { if (position == raw.size()) return 3; endpoint.SetDisplayProvider(number(1) ? Read : nullptr); continue; }
        if (op == 'C') { if (raw.size()-position < 4) return 3; capacity = number(4); if (capacity > 1024) return 3; continue; }
        if (op == 'P') {
            if (raw.size()-position < 5) return 3;
            current.player.revision = number(4);
            current.player.value = static_cast<deskbot_studio_display::Player>(number(1));
            continue;
        }
        if (op == 'S') {
            if (raw.size()-position < 12) return 3;
            current.runtime.application.revision = number(4);
            current.runtime.application.phase = static_cast<deskbot_studio_runtime::Phase>(number(1));
            current.runtime.application.value = number(1);
            current.runtime.page.revision = number(4);
            current.runtime.page.phase = static_cast<deskbot_studio_runtime::Phase>(number(1));
            current.runtime.page.value = number(1);
            continue;
        }
        if (raw.size()-position < 8) return 3;
        const auto now = number(8);
        if (op == 'T') { endpoint.Tick(now); continue; }
        if (op != 'D' || raw.size()-position < 4) return 3;
        const auto size = number(4);
        if (size > raw.size()-position) return 3;
        for (uint64_t i = 0; i < size; ++i) {
            if (endpoint.Feed(static_cast<uint8_t>(raw[position++]), now, reply, capacity))
                std::cout << reply << '\n';
            if (reply[1024] != 'z') return 4;
        }
    }
    std::cerr << reads;
}
