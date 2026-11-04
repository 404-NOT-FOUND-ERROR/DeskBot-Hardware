#include "studio_request_framer.h"

#include <cassert>
#include <fstream>
#include <iostream>
#include <string>

using namespace deskbot_studio_input;
static_assert(sizeof(RequestFramer) <= 2072, "framer must retain bounded storage");

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input) return 3;
    RequestFramer framer;
    constexpr char digits[] = "0123456789abcdef";
    char op;
    while (input.get(op)) {
        if (op == 'R') {
            framer.Reset();
            continue;
        }
        if (op != 'D') return 4;
        std::uint32_t size = 0;
        for (unsigned shift = 0; shift < 32; shift += 8) {
            char byte;
            if (!input.get(byte)) return 5;
            size |= static_cast<std::uint32_t>(static_cast<unsigned char>(byte)) << shift;
        }
        for (std::uint32_t i = 0; i < size; ++i) {
            char byte;
            if (!input.get(byte)) return 6;
            const auto view = framer.Feed(static_cast<unsigned char>(byte));
            assert(framer.BufferedBytes() <= kMaxFrameBytes);
            if (view.kind == FrameKind::TooLarge) {
                std::cout << "overflow\n";
            } else if (view.kind == FrameKind::Line) {
                std::cout << "line ";
                for (std::size_t j = 0; j < view.size; ++j) {
                    std::cout << digits[view.data[j] >> 4] << digits[view.data[j] & 15];
                }
                std::cout << '\n';
            }
        }
    }
    std::cout << "buffered " << framer.BufferedBytes() << '\n';
    return 0;
}
