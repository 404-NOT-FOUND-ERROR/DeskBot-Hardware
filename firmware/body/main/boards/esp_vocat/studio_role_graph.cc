#include "sdkconfig.h"
#if CONFIG_DESKBOT_ROLE_PLAYER_ENABLE
#include "studio_role_graph.h"
#include "studio_asset_layout.h"
#include "studio_role_json.h"
namespace json = deskbot_role_json;
#include <esp_heap_caps.h>
#include <mbedtls/sha256.h>
#include <cmath>
#include <cstring>
#include <new>

namespace deskbot_role {
namespace {
const json::Node* Item(const json::Node* row, const char* key) { return json::GetObjectItemCaseSensitive(row, key); }
const char* Text(const json::Node* row, const char* key) {
    const auto* value = Item(row, key);
    return json::IsString(value) ? value->valuestring : nullptr;
}
bool Eq(const char* a, const char* b) { return a && b && !std::strcmp(a, b); }
bool Fields(const json::Node* row, const char* const* names, size_t count) {
    if (!json::IsObject(row)) return false;
    size_t found = 0;
    for (const auto* field = row->child; field; field = field->next) {
        if (!field->string || ++found > count) return false;
        bool known = false;
        for (size_t i = 0; i < count; ++i) known |= Eq(field->string, names[i]);
        if (!known) return false;
        for (const auto* other = field->next; other; other = other->next)
            if (Eq(field->string, other->string)) return false;
    }
    return found == count;
}
bool Id(const char* name) {
    if (!name || !*name || std::strlen(name) > 64 || *name < 'a' || *name > 'z') return false;
    for (const char* p = name; *p; ++p)
        if (!(*p >= 'a' && *p <= 'z') && !(*p >= '0' && *p <= '9') && *p != '-') return false;
    return true;
}
bool File(const char* name) {
    if (!name) return false;
    const size_t n = std::strlen(name);
    if (n < 5 || n > 31 || std::strcmp(name + n - 4, ".eaf")) return false;
    for (size_t i = 0; i < n - 4; ++i) {
        const char c = name[i];
        if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') &&
            !(c >= '0' && c <= '9') && c != '_') return false;
    }
    return true;
}
bool Hex(const char* text) {
    if (!text || std::strlen(text) != 64) return false;
    for (size_t i = 0; i < 64; ++i)
        if (!(text[i] >= '0' && text[i] <= '9') && !(text[i] >= 'a' && text[i] <= 'f')) return false;
    return true;
}
bool Digest(const uint8_t* data, size_t bytes, const char* text) {
    uint8_t digest[32];
    if (!data || !Hex(text) || mbedtls_sha256(data, bytes, digest, 0)) return false;
    constexpr char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < 32; ++i)
        if (text[i * 2] != hex[digest[i] >> 4] || text[i * 2 + 1] != hex[digest[i] & 15]) return false;
    return true;
}
bool Number(const json::Node* row, const char* key, int low, int high, unsigned& out) {
    const auto* value = Item(row, key);
    if (!json::IsNumber(value) || !std::isfinite(value->valuedouble) || value->valuedouble < low ||
        value->valuedouble > high || value->valuedouble != value->valueint) return false;
    out = unsigned(value->valueint); return true;
}
bool Shape(const uint8_t* raw, size_t bytes) {
    if (!raw || !bytes || bytes > 65536) return false;
    unsigned depth = 0, tokens = 0;
    bool string = false;
    for (size_t i = 0; i < bytes; ++i) {
        const auto c = raw[i];
        if (!c) return false;
        if (string && c == '\\') {
            if (++i >= bytes) return false;
            // cJSON strings are NUL terminated. Refuse an escaped NUL before
            // parsing so a key or ID cannot silently become a shorter one.
            if (raw[i] == 'u' && i + 4 < bytes && !std::memcmp(raw + i + 1, "0000", 4)) return false;
        } else if (c == '"') { string = !string; ++tokens; }
        else if (string) { if (c < 32) return false; }
        else if (c == '[' || c == '{') { if (++depth > 8) return false; ++tokens; }
        else if (c == ']' || c == '}') { if (!depth) return false; --depth; }
        else if (c == ',' || c == ':') ++tokens;
        if (tokens > 32768) return false;
    }
    return !string && !depth;
}
uint32_t U32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
bool ReadClip(emote_handle_t handle, const json::Node* row, Clip& clip, bool& mirror) {
    constexpr const char* keys[] = {"resource_id", "sha256", "frames", "layout_id", "mirror",
                                  "mirror_source", "source_asset_id", "tail_hold_ticks"};
    const char* name = Text(row, "resource_id"), *source = Text(row, "mirror_source"), *asset = Text(row, "source_asset_id");
    const auto* value = Item(row, "mirror");
    unsigned images = 0, hold = 0;
    if (!Fields(row, keys, 8) || !File(name) || !json::IsBool(value) ||
        (!Eq(source, "manual") && !Eq(source, "automatic")) || !asset || !*asset || std::strlen(asset) > 128 ||
        !Number(row, "frames", 1, 511, images) || !Number(row, "tail_hold_ticks", 0, 510, hold)) return false;
    mirror = json::IsTrue(value);
    if (!Eq(Text(row, "layout_id"), mirror ? "eye_anim" : "full_frame")) return false;
    if (emote_get_asset_data_by_name(handle, name, &clip.data, &clip.bytes) != ESP_OK ||
        !Digest(clip.data, clip.bytes, Text(row, "sha256"))) return false;
    bool full = false;
    if (!deskbot_asset_layout::Lookup(handle, clip.data, clip.bytes, full) || full == mirror || clip.bytes < 16) return false;
    const uint32_t records = U32(clip.data + 4);
    if (records < 2 || records > 512 || U32(clip.data + 12) != clip.bytes - 16 || records > (clip.bytes - 16) / 8) return false;
    const size_t start = 16 + size_t(records) * 8, available = clip.bytes - start;
    unsigned seen = 0, metadata = 0;
    for (uint32_t i = 0; i < records; ++i) {
        const uint32_t bytes = U32(clip.data + 16 + i * 8), offset = U32(clip.data + 20 + i * 8);
        if (bytes < 4 || offset > available || bytes > available - offset) return false;
        const auto* frame = clip.data + start + offset;
        if (!std::memcmp(frame, "ZZ_C", 4)) ++metadata;
        else if (!std::memcmp(frame, "ZZ_S", 4)) ++seen;
        else return false;
        // The installed GFX version excludes its last record. Require a tail
        // marker so the last *image* is shown, including a one-image animation.
        if (i + 1 == records && std::memcmp(frame, "ZZ_C", 4)) return false;
    }
    // tail_hold_ticks describes the source preview's terminal hold. Both old
    // and generated files carry one physical terminal record; GFX owns timing.
    if (seen != images || metadata != 1) return false;
    clip.records = static_cast<uint16_t>(records);
    return true;
}
bool ReadExpression(emote_handle_t handle, const json::Node* row, Expression& result, int& home_mirror) {
    constexpr const char* keys[] = {"mode", "fps", "loop", "on_complete", "resources"};
    const auto* loop = Item(row, "loop");
    unsigned fps = 0;
    if (!Fields(row, keys, 5) || !json::IsBool(loop) || !Number(row, "fps", 1, 60, fps)) return false;
    result.fps = static_cast<uint8_t>(fps); result.loop = json::IsTrue(loop);
    result.composite = Eq(Text(row, "mode"), "composable");
    if ((!result.composite && !Eq(Text(row, "mode"), "single")) || (result.composite && !result.loop) ||
        !Eq(Text(row, "on_complete"), result.loop ? "repeat" : "hold_last_frame")) return false;
    const auto* resources = Item(row, "resources");
    constexpr const char* single[] = {"single"};
    constexpr const char* phases[] = {"entry", "loop", "exit"};
    const auto* const* names = result.composite ? phases : single;
    const size_t count = result.composite ? 3 : 1;
    if (!Fields(resources, names, count)) return false;
    for (size_t i = 0; i < count; ++i) {
        bool mirror = false;
        if (!ReadClip(handle, Item(resources, names[i]), result.clips[i], mirror)) return false;
        if (result.composite) {
            if (home_mirror != -1 && home_mirror != int(mirror)) return false;
            home_mirror = int(mirror);
        }
    }
    return true;
}
bool Populate(emote_handle_t handle, const json::Node* root, Graph& graph) {
    constexpr const char* keys[] = {"schema", "package_id", "catalog_id", "default_role_id", "layout",
                                  "required_capabilities", "transition", "roles"};
    constexpr const char* layout_keys[] = {"file", "role_id", "sha256"};
    constexpr const char* transition_keys[] = {"policy", "pending", "interruption"};
    constexpr const char* role_keys[] = {"id", "name", "expressions"};
    constexpr const char* capabilities[] = {"asset_layout_manifest_v1", "role_graph_v1", "role_phase_scheduler_v1"};
    const auto* layout = Item(root, "layout"), *transition = Item(root, "transition"), *caps = Item(root, "required_capabilities");
    if (!Fields(root, keys, 8) || !Eq(Text(root, "schema"), "deskbot.role_graph/v1") ||
        !Id(Text(root, "package_id")) || !Hex(Text(root, "catalog_id")) || !Id(Text(root, "default_role_id")) ||
        !Fields(layout, layout_keys, 3) || !Eq(Text(layout, "file"), "deskbot-layout.json") ||
        !Eq(Text(layout, "role_id"), Text(root, "package_id")) || !Fields(transition, transition_keys, 3) ||
        !Eq(Text(transition, "policy"), "exit_home_entry") || !Eq(Text(transition, "pending"), "latest_target_only") ||
        !Eq(Text(transition, "interruption"), "finish_current_phase") || !json::IsArray(caps) || json::GetArraySize(caps) != 3) return false;
    for (int i = 0; i < 3; ++i) {
        const auto* cap = json::GetArrayItem(caps, i);
        if (!json::IsString(cap) || !Eq(cap->valuestring, capabilities[i])) return false;
    }
    const uint8_t* raw = nullptr; size_t bytes = 0;
    if (emote_get_asset_data_by_name(handle, "deskbot-layout.json", &raw, &bytes) != ESP_OK ||
        bytes > 32768 || !Digest(raw, bytes, Text(layout, "sha256"))) return false;
    json::Document layout_document;
    const auto* manifest = layout_document.Parse(raw, bytes);
    const bool same_package = manifest && Eq(Text(manifest, "role_id"), Text(layout, "role_id"));
    if (!same_package) return false;
    const auto* roles = Item(root, "roles");
    const int count = json::GetArraySize(roles);
    if (!json::IsArray(roles) || count < 1 || count > 4) return false;
    graph.roles = static_cast<Role*>(heap_caps_malloc(size_t(count) * sizeof(Role), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!graph.roles) return false;
    bool initial = false;
    for (const auto* role = roles->child; role; role = role->next) {
        const char* id = Text(role, "id"), *name = Text(role, "name");
        if (!Fields(role, role_keys, 3) || !Id(id) || !name || !*name || std::strlen(name) > 160) return false;
        for (const auto* prior = roles->child; prior != role; prior = prior->next)
            if (Eq(id, Text(prior, "id"))) return false;
        auto& output = graph.roles[graph.count];
        // Value initialization starts each trivially held role in PSRAM.
        new (&output) Role{};
        std::strcpy(output.id, id);
        if (Eq(id, Text(root, "default_role_id"))) { initial = true; graph.initial = graph.count; }
        const auto* expressions = Item(role, "expressions");
        if (!json::IsObject(expressions)) return false;
        // Six daily bindings remain required. Extra expressions are optional,
        // keeping the accepted six-state and original-role packages readable.
        for (const auto* field = expressions->child; field; field = field->next) {
            bool known = false;
            for (size_t i = 0; i < kSemantics; ++i) known |= Eq(field->string, kSemanticNames[i]);
            if (!known) return false;
            for (const auto* other = field->next; other; other = other->next)
                if (Eq(field->string, other->string)) return false;
        }
        int home_mirror = -1;
        for (size_t i = 0; i < kSemantics; ++i) {
            const auto* expression = Item(expressions, kSemanticNames[i]);
            if (!expression && i >= kCoreSemantics) continue;
            if (!ReadExpression(handle, expression, output.expressions[i], home_mirror)) return false;
        }
        ++graph.count;
    }
    return initial;
}
}  // namespace
LoadResult LoadGraph(emote_handle_t handle, Graph& graph) {
    ClearGraph(graph);
    const uint8_t* raw = nullptr; size_t bytes = 0;
    if (emote_get_asset_data_by_name(handle, "deskbot-roles.json", &raw, &bytes) != ESP_OK) return LoadResult::Absent;
    if (!Shape(raw, bytes)) return LoadResult::Invalid;
    json::Document document;
    const auto* root = document.Parse(raw, bytes);
    const bool valid = root && Populate(handle, root, graph);
    if (!valid) { ClearGraph(graph); return LoadResult::Invalid; }
    #if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    uint8_t digest[32]{};
    if (mbedtls_sha256(raw, bytes, digest, 0)) { ClearGraph(graph); return LoadResult::Invalid; }
    constexpr char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < 32; ++i) { graph.sha256[i*2] = hex[digest[i] >> 4]; graph.sha256[i*2+1] = hex[digest[i] & 15]; }
    #endif
    return LoadResult::Applied;
}
void ClearGraph(Graph& graph) { if (graph.roles) heap_caps_free(graph.roles); graph = {}; }
}  // namespace deskbot_role
#endif
