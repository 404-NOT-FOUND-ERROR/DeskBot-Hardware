#include "studio_asset_layout.h"
#include "layout_mock.h"
#include "studio_reflow_metrics.h"
#include <cJSON.h>
#include <mbedtls/platform_util.h>
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <sstream>
#include <vector>
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
#include <cstdlib>
#include <esp_heap_caps.h>
static unsigned allocations = 0;
void* heap_caps_malloc(size_t bytes, unsigned caps) {
    assert(caps == (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (std::getenv("LAYOUT_ALLOC_FAIL")) return nullptr;
    auto* p = std::malloc(bytes); if (p) ++allocations; return p;
}
void heap_caps_free(void* p) { if (p) { assert(allocations); --allocations; std::free(p); } }
void catalog_log(const char*, const char*, ...) {}
static size_t json_calls = 0, json_live = 0, json_peak = 0;
static void* JsonAlloc(size_t bytes) {
    const char* fail = std::getenv("LAYOUT_JSON_FAIL");
    if (fail && ++json_calls == std::strtoul(fail, nullptr, 10)) return nullptr;
    auto* p = static_cast<size_t*>(std::malloc(sizeof(size_t) + bytes));
    if (!p) return nullptr;
    *p = bytes; json_live += bytes; if (json_live > json_peak) json_peak = json_live;
    return p + 1;
}
static void JsonFree(void* memory) {
    if (!memory) return;
    auto* p = static_cast<size_t*>(memory) - 1; assert(json_live >= *p); json_live -= *p; std::free(p);
}
#endif
namespace lay = deskbot_asset_layout;
static std::map<std::string, std::vector<uint8_t>> files;
static std::map<std::string, emoji_data_t> emojis;
extern "C" void mbedtls_platform_zeroize(void* p, size_t n) {
    auto* b = static_cast<volatile unsigned char*>(p); while (n--) *b++ = 0;
}
esp_err_t emote_get_asset_data_by_name(emote_handle_t, const char* name, const uint8_t** data, size_t* size) {
    const auto it = files.find(name); if (it == files.end()) return ESP_ERR_INVALID_ARG;
    *data = it->second.data(); *size = it->second.size(); return ESP_OK;
}
esp_err_t emote_get_emoji_data_by_name(emote_handle_t, const char* name, emoji_data_t** row) {
    const auto it = emojis.find(name); if (it == emojis.end()) return ESP_ERR_INVALID_ARG;
    *row = &it->second; return ESP_OK;
}
int main(int argc, char** argv) {
    assert(argc == 3);
    for (const auto& item : std::filesystem::directory_iterator(argv[1])) {
        if (!item.is_regular_file()) continue;
        std::ifstream f(item.path(), std::ios::binary);
        files.emplace(item.path().filename().string(), std::vector<uint8_t>(std::istreambuf_iterator<char>(f), {}));
    }
    const auto& index = files.at("index.json");
    auto* root = cJSON_ParseWithLength(reinterpret_cast<const char*>(index.data()), index.size());
    const auto text = [](const cJSON* r, const char* k) {
        const auto* p = cJSON_GetObjectItemCaseSensitive(r, k); return cJSON_IsString(p) ? p->valuestring : "";
    };
    const auto* collection = cJSON_GetObjectItemCaseSensitive(root, "emoji_collection");
    for (auto* row = collection ? collection->child : nullptr; row; row = row->next) {
        auto it = files.find(text(row, "file")); if (it == files.end()) continue;
        emojis.emplace(text(row, "name"), emoji_data_t{it->second.data(), it->second.size(), 20, false});
    }
    cJSON_Delete(root);
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    cJSON_Hooks hooks{JsonAlloc, JsonFree}; cJSON_InitHooks(&hooks);
#endif
    int handle;
    const bool configured = lay::Configure(&handle);
    std::istringstream sequence(argv[2]); std::string step;
    while (std::getline(sequence, step, ',')) {
        if (step.rfind("fail:", 0) == 0) { layout_mock::failure = step.substr(5); continue; }
        if (step == "reset") { lay::Reset(&handle); continue; }
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
        if (step == "configure") { lay::Configure(&handle); continue; }
        if (step == "remove-manifest") { files.erase("deskbot-layout.json"); continue; }
        if (step == "corrupt-manifest") { files["deskbot-layout.json"] = {'{'}; continue; }
#endif
        if (step == "no-early-render") { layout_mock::render_before_frame = false; continue; }
        const bool finish = step == "finish", stage = step.rfind("stage:", 0) == 0;
        if (stage) step = step.substr(6);
        const int result = finish ? ESP_OK : lay::Play(&handle, step.c_str());
        if (result == ESP_OK && !stage) layout_mock::Finish(&handle);
        const auto& name = finish ? layout_mock::played : step;
        const auto* e = emojis.count(name) ? &emojis.at(name) : nullptr;
        const bool full = e && lay::FullFrame(&handle, static_cast<const uint8_t*>(e->data), e->size);
        const auto& o = layout_mock::object;
        assert(layout_mock::depth == 0);
        std::cout << "{\"configured\":" << (configured ? "true" : "false") << ",\"result\":" << result
          << ",\"full\":" << (full ? "true" : "false") << ",\"mirror\":" << (o.mirror ? "true" : "false")
          << ",\"x\":" << o.x << ",\"y\":" << o.y << ",\"width\":" << o.width << ",\"height\":" << o.height
          << ",\"calls\":" << layout_mock::calls << ",\"refreshes\":" << layout_mock::refreshes
          << ",\"image_events\":" << layout_mock::image_events << ",\"metadata_events\":" << layout_mock::metadata_events
          << ",\"first_image_covered\":" << (layout_mock::first_image_covered ? "true" : "false")
          << ",\"last_image_covered\":" << (layout_mock::last_image_covered ? "true" : "false")
          << ",\"covered\":" << (layout_mock::covered ? "true" : "false")
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
          << ",\"allocations\":" << allocations << ",\"json_peak_bytes\":" << json_peak
#endif
          << "}\n";
    }
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    lay::Reset(&handle); assert(allocations == 0 && json_live == 0);
#endif
}
