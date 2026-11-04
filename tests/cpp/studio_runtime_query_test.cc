#define CONFIG_DESKBOT_STUDIO_STATE_QUERY_ENABLE 1
#include "studio_usb_session.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

static_assert(sizeof(deskbot_studio_session::Endpoint) <= 3072, "bounded query endpoint");
static deskbot_studio_runtime::Snapshot current;
static unsigned reads = 0;
static deskbot_studio_runtime::Snapshot Read() { ++reads; return current; }

int main(int argc, char** argv) {
    if (argc != 2) return 1;
    std::ifstream input(argv[1], std::ios::binary);
    const std::string raw((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    deskbot_studio_session::Endpoint endpoint;
    const std::string device(32, 'a'), boot(32, 'b'), reboot(32, 'c');
    if (!endpoint.Initialize(device.c_str(), boot.c_str())) return 2;
    endpoint.SetStateProvider(Read);
    size_t position = 0, capacity = 1024;
    const auto number = [&raw, &position](size_t bytes) {
        uint64_t value = 0;
        for (size_t i = 0; i < bytes; ++i)
            value |= static_cast<uint64_t>(static_cast<uint8_t>(raw[position++])) << (8 * i);
        return value;
    };
    char reply[1025];
    reply[1024] = 'z';
    while (position < raw.size()) {
        const char operation = raw[position++];
        if (operation == 'X') { endpoint.Disconnect(); continue; }
        if (operation == 'B') {
            endpoint = deskbot_studio_session::Endpoint{};
            if (!endpoint.Initialize(device.c_str(), reboot.c_str())) return 3;
            endpoint.SetStateProvider(Read);
            continue;
        }
        if (operation == 'P') {
            if (position == raw.size()) return 4;
            endpoint.SetStateProvider(number(1) ? Read : nullptr);
            continue;
        }
        if (operation == 'C') {
            if (raw.size()-position < 4) return 4;
            capacity = number(4);
            if (capacity > 1024) return 4;
            continue;
        }
        if (operation == 'S') {
            if (raw.size()-position < 12) return 4;
            current.application.revision = number(4);
            current.application.phase = static_cast<deskbot_studio_runtime::Phase>(number(1));
            current.application.value = number(1);
            current.page.revision = number(4);
            current.page.phase = static_cast<deskbot_studio_runtime::Phase>(number(1));
            current.page.value = number(1);
            current.controls_available = true;  // Query still exposes read-only capabilities.
            continue;
        }
        if (raw.size()-position < 8) return 4;
        const auto now = number(8);
        if (operation == 'T') { endpoint.Tick(now); continue; }
        if (operation != 'D' || raw.size()-position < 4) return 4;
        const auto size = number(4);
        if (size > raw.size()-position) return 4;
        for (uint64_t i = 0; i < size; ++i) {
            if (endpoint.Feed(static_cast<uint8_t>(raw[position++]), now, reply, capacity))
                std::cout << reply << '\n';
            if (reply[1024] != 'z') return 5;
        }
    }
    std::cerr << reads;
}
