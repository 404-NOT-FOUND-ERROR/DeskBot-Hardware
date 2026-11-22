#pragma once
#include <cstdint>
#include "esp_partition.h"
constexpr int ESP_ERR_INVALID_ARG = 0x102, ESP_ERR_INVALID_STATE = 0x103;
constexpr uint8_t GFX_ALIGN_TOP_MID = 1, GFX_ALIGN_LEFT_MID = 3, GFX_ALIGN_CENTER = 4;
using gfx_coord_t = int16_t;
struct gfx_obj_t {
    gfx_coord_t x = 10, y = 110;
    int width = 340, height = 160, offset_x = 10, offset_y = 10;
    uint8_t align = GFX_ALIGN_LEFT_MID;
    bool mirror = true;
    bool layout_dirty = false, valid_image = false;
};
enum gfx_player_event_t { GFX_PLAYER_EVENT_ONE_FRAME_DONE, GFX_PLAYER_EVENT_ALL_FRAME_DONE };
esp_err_t gfx_anim_set_auto_mirror(gfx_obj_t*, bool);
esp_err_t gfx_obj_set_pos(gfx_obj_t*, gfx_coord_t, gfx_coord_t);
esp_err_t gfx_obj_get_pos(gfx_obj_t*, gfx_coord_t*, gfx_coord_t*);
esp_err_t gfx_obj_get_size(gfx_obj_t*, uint16_t*, uint16_t*);
esp_err_t gfx_obj_align(gfx_obj_t*, uint8_t, gfx_coord_t, gfx_coord_t);
#if CONFIG_DESKBOT_ROLE_PLAYER_ENABLE
esp_err_t gfx_anim_set_src(gfx_obj_t*, const void*, size_t);
esp_err_t gfx_anim_set_segment(gfx_obj_t*, uint32_t, uint32_t, uint32_t, bool);
esp_err_t gfx_anim_start(gfx_obj_t*);
esp_err_t gfx_anim_stop(gfx_obj_t*);
esp_err_t gfx_obj_set_visible(gfx_obj_t*, bool);
bool gfx_obj_get_visible(gfx_obj_t*);
#endif
