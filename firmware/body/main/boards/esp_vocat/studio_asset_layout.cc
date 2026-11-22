#include "sdkconfig.h"
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
#include "studio_asset_layout.h"
#include "expression_emote/emote_api.h"
#include "gfx.h"
#include <cJSON.h>
#include <mbedtls/sha256.h>
#include <cmath>
#include <cstring>
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
#include "studio_asset_layout_manifest.h"
#include <esp_log.h>
#endif

namespace deskbot_asset_layout {
namespace {
struct Layout { uint8_t align = GFX_ALIGN_LEFT_MID; int x = 10, y = 10; bool mirror = true; };
struct Registry {
    emote_handle_t handle = nullptr;
    const uint8_t* full = nullptr;
    size_t bytes = 0;
    Layout original{};
    bool current_full = false;
    uint16_t frames_until_image = 0, reflow_budget = 0;
    uint16_t expected_width = 0, expected_height = 0;
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    manifest::Table layouts{};
#endif
} registry;
constexpr Layout kFull{GFX_ALIGN_CENTER, 0, 0, false};
constexpr char kEatSha[] = "2abe739070ca8e3ae0bd437360f94b24a74d2bf0be6a86eb3c33ba223fef5030";
struct Lock {
    emote_handle_t handle;
    bool held;
    explicit Lock(emote_handle_t h) : handle(h), held(h && emote_lock(h) == ESP_OK) {}
    ~Lock() { if (held) emote_unlock(handle); }
};
const char* Text(const cJSON* row, const char* key) {
    const auto* value = cJSON_GetObjectItemCaseSensitive(row, key);
    return cJSON_IsString(value) ? value->valuestring : nullptr;
}
bool Offset(const cJSON* row, const char* key, int& out) {
    const auto* n = cJSON_GetObjectItemCaseSensitive(row, key);
    if (!cJSON_IsNumber(n) || !std::isfinite(n->valuedouble) || n->valuedouble != n->valueint ||
        n->valueint < -360 || n->valueint > 360) return false;
    out = n->valueint; return true;
}
bool Original(const cJSON* root, Layout& out) {
    const auto* layouts = cJSON_GetObjectItemCaseSensitive(root, "layout");
    if (!cJSON_IsArray(layouts)) return false;
    unsigned count = 0;
    for (const auto* row = layouts->child; row; row = row->next) {
        const char* name = Text(row, "name");
        if (!name || std::strcmp(name, "eye_anim")) continue;
        const char* align = Text(row, "align");
        const char* mirror = Text(cJSON_GetObjectItemCaseSensitive(row, "anim"), "mirror");
        if (++count != 1 || !align || !mirror || !Offset(row, "x", out.x) || !Offset(row, "y", out.y)) return false;
        if (!std::strcmp(align, "GFX_ALIGN_LEFT_MID")) out.align = GFX_ALIGN_LEFT_MID;
        else if (!std::strcmp(align, "GFX_ALIGN_CENTER")) out.align = GFX_ALIGN_CENTER;
        else if (!std::strcmp(align, "GFX_ALIGN_TOP_MID")) out.align = GFX_ALIGN_TOP_MID;
        else return false;
        if (!std::strcmp(mirror, "auto") || !std::strcmp(mirror, "true")) out.mirror = true;
        else if (!std::strcmp(mirror, "false")) out.mirror = false;
        else return false;
    }
    return count == 1 && (!out.mirror || (out.align == GFX_ALIGN_LEFT_MID && out.x >= 0 && out.x <= 170));
}
bool Reviewed(const uint8_t* data, size_t size) {
    if (!data || size != 435957) return false;
    uint8_t digest[32];
    if (mbedtls_sha256(data, size, digest, 0)) return false;
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < 32; ++i)
        if (hex[digest[i] >> 4] != kEatSha[i * 2] || hex[digest[i] & 15] != kEatSha[i * 2 + 1]) return false;
    return true;
}
esp_err_t Apply(gfx_obj_t* object, const Layout& layout, int x, int y) {
    // GFX computes the auto-mirror span from the current X at first-frame
    // preparation. Seed the new X before restarting, so full->eye uses 10.
    auto result = gfx_anim_set_auto_mirror(object, layout.mirror);
    if (result == ESP_OK) result = gfx_obj_set_pos(object, x, y);
    if (result == ESP_OK) result = gfx_obj_align(object, layout.align, layout.x, layout.y);
    return result;
}
uint32_t U32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) | static_cast<uint32_t>(p[1]) << 8 |
           static_cast<uint32_t>(p[2]) << 16 | static_cast<uint32_t>(p[3]) << 24;
}
uint16_t U16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | static_cast<uint16_t>(p[1]) << 8); }
bool FirstImage(const uint8_t* data, size_t size, uint16_t& callbacks, uint16_t& width, uint16_t& height) {
    // Read only the bounded first image's dimensions/timing. Emote remains the decoder.
    if (!data || size < 16 || size > 8 * 1024 * 1024 || data[0] != 0x89 ||
        (std::memcmp(data + 1, "EAF", 3) && std::memcmp(data + 1, "AAF", 3))) return false;
    const uint32_t count = U32(data + 4), payload = U32(data + 12);
    if (!count || count > 512 || payload != size - 16 || count > payload / 8) return false;
    const size_t data_start = 16 + static_cast<size_t>(count) * 8;
    const size_t data_size = size - data_start;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t bytes = U32(data + 16 + i * 8), offset = U32(data + 20 + i * 8);
        if (bytes < 4 || offset > data_size || bytes > data_size - offset) return false;
        const uint8_t* frame = data + data_start + offset;
        if (frame[0] != 'Z' || frame[1] != 'Z') return false;
        if (frame[2] == '_' && frame[3] == 'C') continue;
        if (frame[2] != '_' || frame[3] != 'S' || bytes < 20) return false;
        width = U16(frame + 12); height = U16(frame + 14);
        if (!width || width > 360 || !height || height > 360) return false;
        callbacks = static_cast<uint16_t>(i + 1);
        return true;
    }
    return false;
}
}  // namespace

bool Configure(emote_handle_t handle) {
    Lock lock(handle);
    if (!lock.held) return false;
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    manifest::Clear(registry.layouts);
#endif
    registry = {};
    const uint8_t* data = nullptr;
    size_t size = 0;
    if (emote_get_asset_data_by_name(handle, "index.json", &data, &size) != ESP_OK || !size || size > 16384) return false;
    auto* root = cJSON_ParseWithLength(reinterpret_cast<const char*>(data), size);
    Layout original;
    const bool valid = root && Original(root, original);
    cJSON_Delete(root);
    if (!valid || !emote_get_obj_by_name(handle, "eye_anim")) return false;
    registry.handle = handle;
    registry.original = original;
    // Only reviewed content gets an override. Renames/semantic aliases that
    // resolve to this same loaded data inherit it; replacements get rechecked.
    if (emote_get_asset_data_by_name(handle, "eat.eaf", &data, &size) == ESP_OK && Reviewed(data, size)) {
        registry.full = data; registry.bytes = size;
    }
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    const bool geometry = original.align == GFX_ALIGN_LEFT_MID && original.x == 10 && original.y == 10 && original.mirror;
    const auto state = manifest::Load(handle, registry.layouts);
    if (state == manifest::Result::Applied && geometry) ESP_LOGI("AssetLayout", "Layout manifest applied: %u resources", unsigned(registry.layouts.count));
    else {
        manifest::Clear(registry.layouts);
        if (state != manifest::Result::Absent) ESP_LOGW("AssetLayout", "Layout manifest invalid; using package defaults");
    }
#endif
    return true;
}
void Reset(emote_handle_t handle) {
    Lock lock(handle);
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    if (lock.held && registry.handle == handle) manifest::Clear(registry.layouts);
#endif
    if (lock.held && registry.handle == handle) registry = {};
}
bool FullFrame(emote_handle_t handle, const uint8_t* data, size_t size) {
    Lock lock(handle);
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    if (lock.held && registry.handle == handle)
        return manifest::Full(registry.layouts, data, size, registry.full && registry.full == data && registry.bytes == size);
#endif
    return lock.held && registry.handle == handle && registry.full && registry.full == data && registry.bytes == size;
}
esp_err_t Play(emote_handle_t handle, const char* emotion) {
    if (!handle || !emotion || !*emotion) return ESP_ERR_INVALID_ARG;
    Lock lock(handle);
    if (!lock.held) return ESP_ERR_INVALID_STATE;
    if (registry.handle != handle) return emote_set_anim_emoji(handle, emotion);
    emoji_data_t* row = nullptr;
    auto result = emote_get_emoji_data_by_name(handle, emotion, &row);
    auto* object = emote_get_obj_by_name(handle, "eye_anim");
    if (result != ESP_OK || !row || !object) return result == ESP_OK ? ESP_ERR_INVALID_STATE : result;
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    const bool full = manifest::Full(registry.layouts, static_cast<const uint8_t*>(row->data), row->size,
                                    registry.full && row->data == registry.full && row->size == registry.bytes);
#else
    const bool full = registry.full && row->data == registry.full && row->size == registry.bytes;
#endif
    const bool was_full = registry.current_full;
    // A second ordinary Play can arrive before the first one has resized the
    // full-frame object. Carry that pending reflow onto the newly selected image.
    const bool needs_reflow = full || was_full || registry.frames_until_image != 0;
    const Layout previous = registry.current_full ? kFull : registry.original;
    const Layout target = full ? kFull : registry.original;
    gfx_coord_t old_x = 0, old_y = 0;
    uint16_t callbacks = 0, width = 0, height = 0;
#if CONFIG_DESKBOT_ASSET_REFLOW_ENABLE
    // Equal layout names can still carry different eye heights. Read the new
    // first image before mutating the object; pending switches use the latest
    // dimensions and metadata callback count, under the same recursive lock.
    if (!FirstImage(static_cast<const uint8_t*>(row->data), row->size, callbacks, width, height))
        return ESP_ERR_INVALID_ARG;
    const uint16_t target_width = target.mirror ? static_cast<uint16_t>(360 - 2 * target.x) : width;
    if (!needs_reflow && target_width == registry.expected_width && height == registry.expected_height)
        callbacks = 0;
#else
    if (needs_reflow && !FirstImage(static_cast<const uint8_t*>(row->data), row->size, callbacks, width, height))
        return ESP_ERR_INVALID_ARG;
#endif
    result = gfx_obj_get_pos(object, &old_x, &old_y);
    if (result != ESP_OK) return result;
    // Full-frame is the reviewed 360x360 asset; ordinary assets retain the
    // package's original placement. The original Emote path owns its cache.
    result = Apply(object, target, target.x, target.y);
    if (result == ESP_OK) result = emote_set_anim_emoji(handle, emotion);
    if (result == ESP_OK) {
        registry.current_full = full;
        registry.frames_until_image = callbacks;
        registry.reflow_budget = callbacks ? 512 : 0;
        registry.expected_width = target.mirror ? static_cast<uint16_t>(360 - 2 * target.x) : width;
        registry.expected_height = height;
    }
    else if (Apply(object, previous, old_x, old_y) != ESP_OK) return ESP_ERR_INVALID_STATE;
    return result;
}
#if CONFIG_DESKBOT_ROLE_PLAYER_ENABLE
bool Lookup(emote_handle_t handle, const uint8_t* data, size_t bytes, bool& full) {
    Lock lock(handle);
    if (!lock.held || registry.handle != handle) return false;
    for (size_t i = 0; i < registry.layouts.count; ++i) {
        const auto& row = registry.layouts.entries[i];
        if (row.data == data && row.bytes == bytes) { full = row.full; return true; }
    }
    return false;
}
esp_err_t PlayClip(emote_handle_t handle, const uint8_t* data, size_t bytes,
                   uint16_t records, uint8_t fps, uint16_t start) {
    Lock lock(handle);
    bool full = false;
    if (!lock.held || records < 2 || start >= records - 1 || !fps || fps > 60 ||
        !Lookup(handle, data, bytes, full)) return ESP_ERR_INVALID_ARG;
    auto* object = emote_get_obj_by_name(handle, "eye_anim");
    if (!object) return ESP_ERR_INVALID_STATE;
    uint16_t callbacks = 0, width = 0, height = 0;
    if (!FirstImage(data, bytes, callbacks, width, height)) return ESP_ERR_INVALID_ARG;
    const Layout previous = registry.current_full ? kFull : registry.original;
    const Layout target = full ? kFull : registry.original;
    gfx_coord_t old_x = 0, old_y = 0;
    auto result = gfx_obj_get_pos(object, &old_x, &old_y);
    if (result != ESP_OK) return result;
    // Mounted mmap data stays valid until Reset stops the animation before unmount.
    result = Apply(object, target, target.x, target.y);
    if (result == ESP_OK) result = gfx_anim_set_src(object, data, bytes);
    if (result == ESP_OK) result = gfx_anim_set_segment(object, start, records - 1, fps, false);
    if (result == ESP_OK) result = gfx_anim_start(object);
    if (result == ESP_OK) result = gfx_obj_set_visible(object, true);
    if (result == ESP_OK) {
        registry.current_full = full;
        // A resumed suffix already has palette metadata. Its first image still
        // needs the same geometry refresh; all images in the clip share dimensions.
        registry.frames_until_image = start ? 1 : callbacks;
        registry.reflow_budget = 512;
        registry.expected_width = target.mirror ? static_cast<uint16_t>(360 - 2 * target.x) : width;
        registry.expected_height = height;
    } else {
        gfx_anim_stop(object);
        if (Apply(object, previous, old_x, old_y) != ESP_OK) return ESP_ERR_INVALID_STATE;
    }
    return result;
}
#endif
void OnFrame(emote_handle_t handle, gfx_player_event_t event, const void* current) {
    Lock lock(handle);
    if (!lock.held || registry.handle != handle || !registry.frames_until_image ||
        event != GFX_PLAYER_EVENT_ONE_FRAME_DONE) return;
    auto* object = emote_get_obj_by_name(handle, "eye_anim");
    if (!object || object != current) return;
    if (!registry.reflow_budget) { registry.frames_until_image = 0; return; }
    --registry.reflow_budget;
    if (--registry.frames_until_image) return;
    uint16_t width = 0, height = 0;
    if (gfx_obj_get_size(object, &width, &height) != ESP_OK ||
        width != registry.expected_width || height != registry.expected_height) {
        registry.frames_until_image = 1;
        return;
    }
    const Layout& layout = registry.current_full ? kFull : registry.original;
    if (gfx_obj_align(object, layout.align, layout.x, layout.y) != ESP_OK ||
        emote_notify_all_refresh(handle) != ESP_OK) registry.frames_until_image = 1;
}
}  // namespace deskbot_asset_layout
#endif
