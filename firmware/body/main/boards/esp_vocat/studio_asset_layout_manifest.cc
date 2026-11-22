#include "sdkconfig.h"
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
#include "studio_asset_layout_manifest.h"
#include "studio_role_json.h"
namespace json = deskbot_role_json;
#include <esp_heap_caps.h>
#include <mbedtls/sha256.h>
#include <cmath>
#include <cstring>

namespace deskbot_asset_layout::manifest {
namespace {
constexpr size_t kMaxBytes = 32768, kMaxEntries = 128;
const char* Text(const json::Node* row, const char* key) {
    const auto* item = json::GetObjectItemCaseSensitive(row, key);
    return json::IsString(item) ? item->valuestring : nullptr;
}
bool Equal(const char* a, const char* b) { return a && b && !std::strcmp(a, b); }
bool Fields(const json::Node* row, const char* const* names, size_t count) {
    if (!json::IsObject(row)) return false;
    size_t found = 0;
    for (const auto* field = row->child; field; field = field->next) {
        if (!field->string || ++found > count) return false;
        bool known = false;
        for (size_t i = 0; i < count; ++i) known |= Equal(field->string, names[i]);
        if (!known) return false;
        for (const auto* other = field->next; other; other = other->next)
            if (Equal(field->string, other->string)) return false;
    }
    return found == count;
}
bool Role(const char* name) {
    if (!name || *name < 'a' || *name > 'z' || std::strlen(name) > 64) return false;
    for (const char* p = name; *p; ++p)
        if (!(*p >= 'a' && *p <= 'z') && !(*p >= '0' && *p <= '9') && *p != '-') return false;
    return true;
}
bool File(const char* name) {
    if (!name) return false;
    const size_t size = std::strlen(name);
    if (size < 5 || size > 31 || std::strcmp(name + size - 4, ".eaf")) return false;
    for (size_t i = 0; i < size - 4; ++i) {
        const char c = name[i];
        if (!(c >= 'a' && c <= 'z') && !(c >= 'A' && c <= 'Z') &&
            !(c >= '0' && c <= '9') && c != '_') return false;
    }
    return true;
}
bool Dimension(const json::Node* row, const char* key, uint16_t& out) {
    const auto* value = json::GetObjectItemCaseSensitive(row, key);
    if (!json::IsNumber(value) || !std::isfinite(value->valuedouble) || value->valuedouble < 1 ||
        value->valuedouble > 360 || value->valuedouble != value->valueint) return false;
    out = static_cast<uint16_t>(value->valueint); return true;
}
bool Shape(const uint8_t* raw, size_t bytes) {
    if (!raw || !bytes || bytes > kMaxBytes) return false;
    unsigned depth = 0, tokens = 0;
    bool string = false;
    for (size_t i = 0; i < bytes; ++i) {
        const uint8_t c = raw[i];
        // Manifest identifiers are ASCII. Rejecting escapes prevents embedded
        // NULs from shortening keys, IDs or digests after JSON decoding.
        if (!c || c > 127 || c == '\\') return false;
        if (c == '"') { string = !string; ++tokens; }
        else if (string) { if (c < 32) return false; }
        else if (c == '[' || c == '{') { if (++depth > 4) return false; ++tokens; }
        else if (c == ']' || c == '}') { if (!depth) return false; --depth; }
        else if (c == ',' || c == ':') ++tokens;
        if (tokens > 16384) return false;
    }
    return !string && !depth;
}
uint32_t U32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
uint16_t U16(const uint8_t* p) { return uint16_t(p[0] | uint16_t(p[1]) << 8); }
bool Frames(const uint8_t* raw, size_t bytes, uint16_t width, uint16_t height) {
    if (!raw || bytes < 16 || bytes > 8 * 1024 * 1024 || raw[0] != 0x89 ||
        (std::memcmp(raw + 1, "EAF", 3) && std::memcmp(raw + 1, "AAF", 3))) return false;
    const uint32_t count = U32(raw + 4), payload = U32(raw + 12);
    if (!count || count > 512 || payload != bytes - 16 || count > payload / 8) return false;
    const size_t start = 16 + size_t(count) * 8, available = bytes - start;
    bool image = false;
    for (uint32_t i = 0; i < count; ++i) {
        const uint32_t size = U32(raw + 16 + i * 8), offset = U32(raw + 20 + i * 8);
        if (size < 4 || offset > available || size > available - offset) return false;
        const auto* frame = raw + start + offset;
        if (std::memcmp(frame, "ZZ_C", 4) == 0) continue;
        if (size < 20 || std::memcmp(frame, "ZZ_S", 4) ||
            U16(frame + 12) != width || U16(frame + 14) != height) return false;
        image = true;
    }
    return image;
}
bool Digest(const uint8_t* data, size_t bytes, const char* text) {
    if (!text || std::strlen(text) != 64) return false;
    for (size_t i = 0; i < 64; ++i)
        if (!(text[i] >= '0' && text[i] <= '9') && !(text[i] >= 'a' && text[i] <= 'f')) return false;
    uint8_t digest[32];
    if (mbedtls_sha256(data, bytes, digest, 0)) return false;
    constexpr char hex[] = "0123456789abcdef";
    for (unsigned i = 0; i < 32; ++i)
        if (text[i * 2] != hex[digest[i] >> 4] || text[i * 2 + 1] != hex[digest[i] & 15]) return false;
    return true;
}
bool Populate(emote_handle_t handle, const json::Node* root, Table& table) {
    constexpr const char* top[] = {"schema", "role_id", "assets"};
    constexpr const char* keys[] = {"resource_id", "resource_sha256", "width", "height", "layout_id", "mirror", "source"};
    if (!Fields(root, top, 3) || !Equal(Text(root, "schema"), "deskbot.asset_layouts/v1") ||
        !Role(Text(root, "role_id"))) return false;
    const auto* rows = json::GetObjectItemCaseSensitive(root, "assets");
    const int count = json::GetArraySize(rows);
    if (!json::IsArray(rows) || count < 1 || count > int(kMaxEntries)) return false;
    table.entries = static_cast<Entry*>(heap_caps_malloc(size_t(count) * sizeof(Entry), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!table.entries) return false;
    for (const auto* row = rows->child; row; row = row->next) {
        const char* name = Text(row, "resource_id"), *layout = Text(row, "layout_id"), *source = Text(row, "source");
        const auto* mirror = json::GetObjectItemCaseSensitive(row, "mirror");
        uint16_t width = 0, height = 0;
        if (!Fields(row, keys, 7) || !File(name) || !json::IsBool(mirror) ||
            (!Equal(source, "manual") && !Equal(source, "automatic")) ||
            !Dimension(row, "width", width) || !Dimension(row, "height", height)) return false;
        const bool status = Equal(name, "listen.eaf"), full = !json::IsTrue(mirror);
        if (status ? (!Equal(layout, "listen_anim") || !full || !Equal(source, "automatic")) :
            !Equal(layout, full ? "full_frame" : "eye_anim")) return false;
        for (const auto* previous = rows->child; previous != row; previous = previous->next)
            if (Equal(name, Text(previous, "resource_id"))) return false;
        const uint8_t* data = nullptr; size_t bytes = 0;
        if (emote_get_asset_data_by_name(handle, name, &data, &bytes) != ESP_OK ||
            !Frames(data, bytes, width, height) || !Digest(data, bytes, Text(row, "resource_sha256"))) return false;
        if (!status) table.entries[table.count++] = Entry{data, bytes, full};
    }
    return true;
}
}  // namespace

Result Load(emote_handle_t handle, Table& table) {
    Clear(table);
    const uint8_t* raw = nullptr; size_t bytes = 0;
    if (emote_get_asset_data_by_name(handle, "deskbot-layout.json", &raw, &bytes) != ESP_OK) return Result::Absent;
    if (!Shape(raw, bytes)) return Result::Invalid;
    json::Document document;
    const auto* root = document.Parse(raw, bytes);
    const bool valid = root && Populate(handle, root, table);
    if (!valid) { Clear(table); return Result::Invalid; }
    return Result::Applied;
}
void Clear(Table& table) {
    if (table.entries) heap_caps_free(table.entries);
    table = {};
}
bool Full(const Table& table, const uint8_t* data, size_t bytes, bool legacy) {
    for (size_t i = 0; i < table.count; ++i)
        if (table.entries[i].data == data && table.entries[i].bytes == bytes) return table.entries[i].full;
    return legacy;
}
}  // namespace deskbot_asset_layout::manifest
#endif
