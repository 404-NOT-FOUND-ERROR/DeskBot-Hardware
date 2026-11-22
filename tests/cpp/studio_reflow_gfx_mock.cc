#include "expression_emote/emote_api.h"
#include "layout_mock.h"
#include "studio_asset_layout.h"
#include "studio_reflow_metrics.h"
#include <algorithm>
#include <cassert>
#include <cstring>
namespace layout_mock {
gfx_obj_t object;
int depth = 0, calls = 0, refreshes = 0, image_events = 0, metadata_events = 0;
std::string failure, played;
bool covered = true, render_before_frame = true;
bool first_image_covered = true, last_image_covered = true;
static bool have_image = false;
}
using namespace layout_mock;
static const emoji_data_t* staged = nullptr;
static int dirty_x1 = 360, dirty_y1 = 360, dirty_x2 = 0, dirty_y2 = 0;
static void Invalidate() {
    dirty_x1 = std::min<int>(dirty_x1, object.x); dirty_y1 = std::min<int>(dirty_y1, object.y);
    dirty_x2 = std::max<int>(dirty_x2, object.x + object.width); dirty_y2 = std::max<int>(dirty_y2, object.y + object.height);
}
static void Align() {
    object.x = (object.align == GFX_ALIGN_LEFT_MID ? 0 : (360 - object.width) / 2) + object.offset_x;
    object.y = (object.align == GFX_ALIGN_TOP_MID ? 0 : (360 - object.height) / 2) + object.offset_y;
}
static void Render() {
    if (object.layout_dirty) {
        Invalidate(); Align(); Invalidate(); object.layout_dirty = false;
    }
    if (object.valid_image) {
        Align();
        const bool frame_covered = dirty_x1 <= std::max<int>(0, object.x) && dirty_y1 <= std::max<int>(0, object.y) &&
            dirty_x2 >= std::min<int>(360, object.x + object.width) && dirty_y2 >= std::min<int>(360, object.y + object.height);
        if (!have_image) { first_image_covered = frame_covered; have_image = true; }
        last_image_covered = frame_covered;
        covered = covered && frame_covered;
    }
    dirty_x1 = dirty_y1 = 360; dirty_x2 = dirty_y2 = 0;
}
static bool Fail(const char* name) {
    if (failure != name) return false;
    failure.clear(); return true;
}
esp_err_t emote_lock(emote_handle_t) {
    if (Fail("lock")) return ESP_ERR_INVALID_STATE;
    ++depth; return ESP_OK;
}
esp_err_t emote_unlock(emote_handle_t) { assert(depth > 0); --depth; return ESP_OK; }
gfx_obj_t* emote_get_obj_by_name(emote_handle_t, const char* name) {
    assert(depth > 0);
    return std::strcmp(name, "eye_anim") || Fail("object") ? nullptr : &object;
}
esp_err_t gfx_anim_set_auto_mirror(gfx_obj_t* obj, bool mirror) {
    assert(depth > 0); if (Fail("mirror")) return ESP_ERR_INVALID_STATE;
    obj->mirror = mirror; return ESP_OK;
}
esp_err_t gfx_obj_set_pos(gfx_obj_t* obj, gfx_coord_t x, gfx_coord_t y) {
    assert(depth > 0); if (Fail("position")) return ESP_ERR_INVALID_STATE;
    Invalidate(); obj->x = x; obj->y = y; Invalidate(); return ESP_OK;
}
esp_err_t gfx_obj_get_pos(gfx_obj_t* obj, gfx_coord_t* x, gfx_coord_t* y) {
    assert(depth > 0); if (Fail("getpos")) return ESP_ERR_INVALID_STATE;
    *x = obj->x; *y = obj->y; return ESP_OK;
}
esp_err_t gfx_obj_get_size(gfx_obj_t* obj, uint16_t* w, uint16_t* h) {
    assert(depth > 0); if (Fail("getsize")) return ESP_ERR_INVALID_STATE;
    *w = obj->width; *h = obj->height; return ESP_OK;
}
esp_err_t gfx_obj_align(gfx_obj_t* obj, uint8_t align, gfx_coord_t x, gfx_coord_t y) {
    assert(depth > 0); if (Fail("align")) return ESP_ERR_INVALID_STATE;
    Invalidate(); obj->align = align; obj->offset_x = x; obj->offset_y = y; obj->layout_dirty = true; return ESP_OK;
}
esp_err_t emote_set_anim_emoji(emote_handle_t handle, const char* name) {
    assert(depth > 0); ++calls;
    if (Fail("play")) return ESP_ERR_INVALID_STATE;
    emoji_data_t* row = nullptr;
    if (emote_get_emoji_data_by_name(handle, name, &row) != ESP_OK) return ESP_ERR_INVALID_ARG;
    played = name;
    staged = row; object.valid_image = false;
    Invalidate();
    return ESP_OK;
}
esp_err_t emote_notify_all_refresh(emote_handle_t) {
    assert(depth > 0); if (Fail("refresh")) return ESP_ERR_INVALID_STATE;
    ++refreshes; dirty_x1 = dirty_y1 = 0; dirty_x2 = dirty_y2 = 360; return ESP_OK;
}
static uint32_t U32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1])<<8 | uint32_t(p[2])<<16 | uint32_t(p[3])<<24; }
void layout_mock::Finish(emote_handle_t handle) {
    assert(depth == 0 && staged);
    covered = true;
    first_image_covered = last_image_covered = true; have_image = false;
    if (render_before_frame) Render();
    // Unrelated callbacks must not consume the pending eye-image counter.
    gfx_obj_t unrelated;
    deskbot_asset_layout::OnFrame(handle, GFX_PLAYER_EVENT_ONE_FRAME_DONE, &unrelated);
    deskbot_asset_layout::OnFrame(handle, GFX_PLAYER_EVENT_ALL_FRAME_DONE, &object);
    const auto* data = static_cast<const uint8_t*>(staged->data);
    const unsigned count = U32(data + 4);
    for (unsigned i = 0; i < count && i < 4; ++i) {
        const uint8_t* frame = data + 16 + count * 8 + U32(data + 20 + i * 8);
        const bool image = frame[2] == '_' && frame[3] == 'S';
        if (image) {
            const int w = frame[12] | int(frame[13]) << 8;
            object.height = frame[14] | int(frame[15]) << 8;
            object.width = object.mirror ? 360 - 2 * object.x : w;
            object.valid_image = true; ++image_events;
        } else ++metadata_events;
        deskbot_asset_layout::OnFrame(handle, GFX_PLAYER_EVENT_ONE_FRAME_DONE, &object);
        Invalidate(); Render();
    }
}
