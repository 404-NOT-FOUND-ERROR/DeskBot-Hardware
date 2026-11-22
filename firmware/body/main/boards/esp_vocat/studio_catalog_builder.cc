#include "sdkconfig.h"
#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE
#include "studio_catalog.h"
#include "studio_catalog_actions.h"
#include <cJSON.h>
#include <algorithm>
#include <cmath>

namespace deskbot_studio_catalog {
namespace {
bool Name(const char* s, size_t limit = 31, bool digit_first = false) {
    if (!s || !*s || std::strlen(s) > limit) return false;
    for (size_t i = 0; s[i]; ++i) {
        const char c = s[i];
        const bool alpha = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
        if (!alpha && !((i || digit_first) && ((c >= '0' && c <= '9') || c == '_'))) return false;
    }
    return true;
}
bool File(const char* s) {
    if (!s) return false;
    const size_t length = std::strlen(s);
    if (length < 5 || length > 31 || std::strcmp(s + length - 4, ".eaf")) return false;
    char stem[28]{};
    std::memcpy(stem, s, length - 4);
    return Name(stem, 27, true);
}
const cJSON* Field(const cJSON* object, const char* name) {
    return cJSON_GetObjectItemCaseSensitive(object, name);
}
const char* Text(const cJSON* object, const char* name) {
    const auto* value = Field(object, name);
    return cJSON_IsString(value) ? value->valuestring : nullptr;
}
bool Integer(const cJSON* value, int low, int high) {
    return cJSON_IsNumber(value) && std::isfinite(value->valuedouble) &&
        value->valuedouble >= low && value->valuedouble <= high &&
        value->valuedouble == value->valueint;
}
// Limit depth/tokens before cJSON allocates; preserve its global allocator.
bool Shape(const uint8_t* input, size_t size) {
    if (!input || !size || size > kMaxIndexBytes) return false;
    unsigned depth = 0, tokens = 0;
    bool string = false, escape = false;
    for (size_t i = 0; i < size; ++i) {
        const auto c = input[i];
        if (!c || c > 127) return false;
        if (string) {
            if (escape) {
                // cJSON stores decoded strings as C strings. Reject Unicode
                // escapes here so embedded NULs cannot shorten IDs or keys.
                if (c == 'u') return false;
                escape = false;
            }
            else if (c == '\\') escape = true;
            else if (c == '"') string = false;
            else if (c < 32) return false;
        } else if (c == '"') { string = true; ++tokens; }
        else if (c == '[' || c == '{') { if (++depth > 12) return false; ++tokens; }
        else if (c == ']' || c == '}') { if (!depth) return false; --depth; }
        else if (c == ',' || c == ':') ++tokens;
        if (tokens > 2048) return false;
    }
    return !string && !depth;
}
bool Unique(const cJSON* value) {
    if (cJSON_IsObject(value)) {
        for (const cJSON* a = value->child; a; a = a->next) {
            if (!a->string) return false;
            for (const cJSON* b = a->next; b; b = b->next)
                if (!b->string || !std::strcmp(a->string, b->string)) return false;
        }
    }
    for (const cJSON* child = value->child; child; child = child->next)
        if (!Unique(child)) return false;
    return true;
}
bool Hash(Digest hash, const void* data, size_t size, char* output) {
    return hash.start(hash.context) && hash.update(hash.context, data, size) &&
        hash.finish(hash.context, output) && deskbot_studio_link::Hex(output, 64);
}
Entry* Add(Snapshot& snapshot, const char* kind, const char* id) {
    if (snapshot.count == kMaxEntries || !Name(id)) return nullptr;
    auto& row = snapshot.entries[snapshot.count];
    std::snprintf(row.key, sizeof(row.key), "%s:%s", kind, id);
    for (unsigned i = 0; i < snapshot.count; ++i)
        if (!std::strcmp(snapshot.entries[i].key, row.key)) return nullptr;
    ++snapshot.count;
    return &row;
}
bool Animation(Snapshot& snapshot, const cJSON* value, bool status, Source source, Digest hash) {
    const char* id = Text(value, "name"), *file = Text(value, "file");
    if (!Name(id) || !File(file) || (status != !std::strcmp(id, "listen"))) return false;
    const auto* eaf = Field(value, "eaf");
    const auto* fps = Field(eaf, "fps"), *loop = Field(eaf, "loop");
    if (!status && (!Integer(fps, 1, 60) || !cJSON_IsBool(loop))) return false;
    const unsigned speed = status ? 15 : static_cast<unsigned>(fps->valueint);
    const bool repeats = status || cJSON_IsTrue(loop);
    const uint8_t* data = nullptr;
    size_t size = 0;
    if (!source.asset(source.context, file, data, size) || !data || !size || size > 8 * 1024 * 1024 ||
        !source.parsed(source.context, id, data, size, speed, repeats, status)) return false;
    auto* row = Add(snapshot, status ? "status" : "expression", id);
    if (!row) return false;
    row->bytes = static_cast<uint32_t>(size);
    std::strcpy(row->resource, file);
    bool shared = false;
    for (unsigned i = 0; i + 1 < snapshot.count; ++i) {
        const auto& old = snapshot.entries[i];
        if (!std::strcmp(old.resource, file)) {
            if (old.bytes != row->bytes) return false;
            std::strcpy(row->digest, old.digest);
            shared = true;
            break;
        }
    }
    if (!shared && !Hash(hash, data, size, row->digest)) return false;
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
    const bool full_frame = !status && source.full_frame && source.full_frame(source.context, data, size);
#endif
    const char* action = nullptr;
    for (const auto& binding : kBindings)
        if (!std::strcmp(binding.semantic, id)) action = binding.action;
    char action_json[40] = "null";
    if (action) std::snprintf(action_json, sizeof(action_json), "\"%s\"", action);
    const int count = std::snprintf(row->json, sizeof(row->json),
        "{\"bytes\":%" PRIu32 ",\"fps\":%u,\"id\":\"%s\",\"implicit_action_id\":%s,\"kind\":\"%s\","
        "\"layout_id\":\"%s\",\"loop\":%s,\"resource_id\":\"%s\",\"resource_sha256\":\"%s\"}",
        row->bytes, speed, id, action_json, status ? "status" : "expression",
        status ? "listen_anim" :
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
        full_frame ? "full_frame" :
#endif
        "eye_anim", repeats ? "true" : "false", file, row->digest);
    return count > 0 && static_cast<size_t>(count) < sizeof(row->json);
}
bool Layout(Snapshot& snapshot, const cJSON* layouts, const char* id) {
    const cJSON* found = nullptr;
    for (const cJSON* row = layouts->child; row; row = row->next) {
        const char* name = Text(row, "name");
        if (name && !std::strcmp(name, id)) { if (found) return false; found = row; }
    }
    if (!found) return false;
    const char* align = Text(found, "align"), *mirror = Text(Field(found, "anim"), "mirror");
    const char* type = Text(found, "type");
    const auto* x = Field(found, "x"), *y = Field(found, "y");
    if (!type || std::strcmp(type, "anim") || !align || !mirror ||
        (std::strcmp(align, "GFX_ALIGN_LEFT_MID") && std::strcmp(align, "GFX_ALIGN_TOP_MID") && std::strcmp(align, "GFX_ALIGN_CENTER")) ||
        (std::strcmp(mirror, "auto") && std::strcmp(mirror, "false") && std::strcmp(mirror, "true")) ||
        !Integer(x, -360, 360) || !Integer(y, -360, 360)) return false;
    auto* entry = Add(snapshot, "layout", id);
    if (!entry) return false;
    const int count = std::snprintf(entry->json, sizeof(entry->json),
        "{\"align\":\"%s\",\"id\":\"%s\",\"kind\":\"layout\",\"mirror\":\"%s\",\"x\":%d,\"y\":%d}",
        align, id, mirror, x->valueint, y->valueint);
    return count > 0 && static_cast<size_t>(count) < sizeof(entry->json);
}
bool Populate(Snapshot& snapshot, const cJSON* root, Source source, Digest hash) {
    const auto* ordinary = Field(root, "emoji_collection"), *icons = Field(root, "icon_collection"), *layouts = Field(root, "layout");
    if (!Integer(Field(root, "version"), 1, 1) || !cJSON_IsArray(ordinary) || !ordinary->child ||
        !cJSON_IsArray(icons) || !cJSON_IsArray(layouts)) return false;
    for (const cJSON* row = ordinary->child; row; row = row->next)
        if (!Animation(snapshot, row, false, source, hash)) return false;
    unsigned listens = 0;
    for (const cJSON* row = icons->child; row; row = row->next) {
        const char* id = Text(row, "name");
        if (id && !std::strcmp(id, "listen")) {
            if (++listens != 1 || !Animation(snapshot, row, true, source, hash)) return false;
        }
    }
    if (listens != 1 || !Layout(snapshot, layouts, "eye_anim") || !Layout(snapshot, layouts, "listen_anim")) return false;
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
    bool full_frame = false;
    for (unsigned i = 0; i < snapshot.count; ++i)
        if (std::strstr(snapshot.entries[i].json, "\"layout_id\":\"full_frame\"")) full_frame = true;
    if (full_frame) {
        auto* row = Add(snapshot, "layout", "full_frame");
        if (!row) return false;
        std::strcpy(row->json, "{\"align\":\"GFX_ALIGN_CENTER\",\"id\":\"full_frame\",\"kind\":\"layout\",\"mirror\":\"false\",\"x\":0,\"y\":0}");
    }
#endif
    for (const auto& action : kActions) {
        auto* row = Add(snapshot, "action", action.id);
        if (!row) return false;
        std::snprintf(row->json, sizeof(row->json),
            "{\"completion\":\"fact_only\",\"id\":\"%s\",\"kind\":\"action\",\"wire_command\":2,\"wire_value\":%u}",
            action.id, action.value);
    }
    std::sort(snapshot.entries, snapshot.entries + snapshot.count,
        [](const Entry& a, const Entry& b) { return std::strcmp(a.key, b.key) < 0; });
    return true;
}
}  // namespace

bool Build(Snapshot& snapshot, const uint8_t* index, size_t size, const char* package,
           Source source, Digest hash) {
    snapshot.count = 0;
    snapshot.catalog[0] = 0;
    if (!Shape(index, size) || !deskbot_studio_link::Hex(package, 64) || !source.asset || !source.parsed ||
        !hash.start || !hash.update || !hash.finish) return false;
    const char* end = nullptr;
    cJSON* root = cJSON_ParseWithLengthOpts(reinterpret_cast<const char*>(index), size, &end, false);
    bool valid = root && cJSON_IsObject(root) && Unique(root);
    if (end) while (end < reinterpret_cast<const char*>(index) + size && (*end == ' ' || *end == '\r' || *end == '\n' || *end == '\t')) ++end;
    valid = valid && end == reinterpret_cast<const char*>(index) + size;
    if (valid) valid = Populate(snapshot, root, source, hash);
    cJSON_Delete(root);
    if (!valid || !Hash(hash, index, size, snapshot.index)) { snapshot.count = 0; return false; }
    std::strcpy(snapshot.package, package);
    if (!hash.start(hash.context)) { snapshot.count = 0; return false; }
    const auto append = [&](const char* s) { return hash.update(hash.context, s, std::strlen(s)); };
    valid = append("{\"entries\":[");
    for (unsigned i = 0; valid && i < snapshot.count; ++i)
        valid = (!i || append(",")) && append(snapshot.entries[i].json);
    valid = valid && append("],\"index_sha256\":\"") && append(snapshot.index) &&
        append("\",\"package_sha256\":\"") && append(snapshot.package) &&
        append("\",\"schema\":\"deskbot.installed_expression_manifest/v1\"}") &&
        hash.finish(hash.context, snapshot.catalog) && deskbot_studio_link::Hex(snapshot.catalog, 64);
    if (!valid) snapshot.count = 0;
    return valid;
}
}  // namespace deskbot_studio_catalog
#endif
