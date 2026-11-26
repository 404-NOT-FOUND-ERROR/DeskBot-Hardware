#include "listen_overlay_rgb565.h"
#include <cassert>
#include <cstdint>

int main() {
    for (unsigned background = 0; background <= 0xffff; ++background) {
        assert(airboat_listen_over_rgb565(uint16_t(background), 0) == background);
        assert(airboat_listen_over_rgb565(uint16_t(background), 0xffff) == 0xffff);
        unsigned previous_r = 0, previous_g = 0, previous_b = 0;
        for (unsigned gray = 0; gray <= 255; ++gray) {
            const uint16_t mask = uint16_t(((gray * 31 + 127) / 255) << 11 |
                ((gray * 63 + 127) / 255) << 5 | ((gray * 31 + 127) / 255));
            const uint16_t out = airboat_listen_over_rgb565(uint16_t(background), mask);
            const unsigned r = (out >> 11) & 31, g = (out >> 5) & 63, b = out & 31;
            assert(r >= previous_r && g >= previous_g && b >= previous_b);
            previous_r = r; previous_g = g; previous_b = b;
        }
    }
}
