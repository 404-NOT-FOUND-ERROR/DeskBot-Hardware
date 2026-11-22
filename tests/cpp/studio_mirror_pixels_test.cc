#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <vector>
using gfx_coord_t = int;
struct gfx_color_t { uint16_t full; };
struct gfx_area_t { int x1, y1, x2, y2; };
struct eaf_header_t { int width; };
enum gfx_mirror_mode_t { GFX_MIRROR_DISABLED, GFX_MIRROR_AUTO, GFX_MIRROR_MANUAL };
#define GFX_PALETTE_CACHE_UNINITIALIZED 0xffffffffu
#define GFX_PALETTE_SET_TRANSPARENT() 0x10000u
#define GFX_PALETTE_SET_COLOR(color) uint32_t(color)
#define GFX_PALETTE_IS_TRANSPARENT(value) ((value) & 0x10000u)
#define GFX_PALETTE_GET_COLOR(value) ((value) & 0xffffu)
static bool eaf_palette_get_color(const eaf_header_t*, uint8_t index, bool, gfx_color_t* color) {
    color->full = index; return index == 0;
}
// The test compiles the exact 4/8/24-bit render functions from locked GFX.
#include "gfx_mirror_pixels.inc"
int main(int argc, char** argv) {
    assert(argc == 4); const int width = std::stoi(argv[1]), bits = std::stoi(argv[2]), transparent = std::stoi(argv[3]);
    assert(width >= 1 && width <= 360 && (bits == 4 || bits == 8 || bits == 24));
    constexpr int height = 3;
    std::vector<uint8_t> indexed(width * height);
    std::vector<uint8_t> packed(width * height / 2);
    std::vector<uint16_t> colors(width * height);
    std::vector<gfx_color_t> result(360 * height, gfx_color_t{0});
    for (int y = 0; y < height; ++y) for (int x = 0; x < width; ++x) {
        indexed[y * width + x] = transparent && x % 3 == 0 ? 0 : (x + y * 7) % (bits == 4 ? 15 : 255) + 1;
        if (bits == 4) packed[(y * width + x) / 2] |= indexed[y * width + x] << (x % 2 ? 0 : 4);
        colors[y * width + x] = uint16_t(x + y * 700);
    }
    eaf_header_t header{width}; gfx_area_t clip{10, 0, 10 + std::min(width, 340), height};
    uint32_t palette[256]; std::fill(palette, palette + 256, GFX_PALETTE_CACHE_UNINITIALIZED);
    if (bits == 4) gfx_anim_render_4bit_pixels(result.data() + 10, 360, packed.data(), width,
        &header, palette, &clip, false, GFX_MIRROR_AUTO, 0, 10);
    else if (bits == 8) gfx_anim_render_8bit_pixels(result.data() + 10, 360, indexed.data(), width,
        &header, palette, &clip, false, GFX_MIRROR_AUTO, 0, 10);
    else gfx_anim_render_24bit_pixels(result.data() + 10, 360, reinterpret_cast<uint8_t*>(colors.data()), width,
        &header, palette, &clip, false, GFX_MIRROR_AUTO, 0, 10);
    for (const auto& color : result) std::cout << color.full << ' ';
}
