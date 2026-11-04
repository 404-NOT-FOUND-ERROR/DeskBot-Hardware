#include "studio_usb_session.h"
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

static_assert(sizeof(deskbot_studio_session::Endpoint) <= 3072, "bounded static receiver");
static_assert(sizeof(deskbot_studio_session::Request) <= 704, "bounded request stack");

int main(int argc, char** argv) {
    if (argc != 2) return 1;
    std::ifstream input(argv[1], std::ios::binary);
    const std::string raw((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
    deskbot_studio_session::Endpoint endpoint;
    const std::string device(32, 'a'), boot(32, 'b');
    if (!endpoint.Initialize(device.c_str(), boot.c_str())) return 2;
    size_t position = 0;
    const auto number = [&raw, &position](size_t bytes) {
        uint64_t value = 0;
        for (size_t i = 0; i < bytes; ++i) {
            value |= static_cast<uint64_t>(static_cast<uint8_t>(raw[position++])) << (8 * i);
        }
        return value;
    };
    char reply[1024];
    while (position < raw.size()) {
        const char operation = raw[position++];
        if (operation == 'X') { endpoint.Disconnect(); continue; }
        if (raw.size() - position < 8) return 3;
        const auto now = number(8);
        if (operation == 'T') { endpoint.Tick(now); continue; }
        if (operation != 'D' || raw.size() - position < 4) return 4;
        const auto size = number(4);
        if (size > raw.size() - position) return 5;
        for (uint64_t i = 0; i < size; ++i) {
            if (endpoint.Feed(static_cast<uint8_t>(raw[position++]), now, reply, sizeof(reply))) {
                std::cout << reply << '\n';
            }
        }
    }
}
