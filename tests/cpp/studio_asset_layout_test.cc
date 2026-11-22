#include "studio_asset_layout.h"
#include "layout_mock.h"
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
    int handle;
    const bool configured = lay::Configure(&handle);
    std::istringstream sequence(argv[2]); std::string step;
    while (std::getline(sequence, step, ',')) {
        if (step.rfind("fail:", 0) == 0) { layout_mock::failure = step.substr(5); continue; }
        if (step == "reset") { lay::Reset(&handle); continue; }
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
          << ",\"covered\":" << (layout_mock::covered ? "true" : "false") << "}\n";
    }
}
