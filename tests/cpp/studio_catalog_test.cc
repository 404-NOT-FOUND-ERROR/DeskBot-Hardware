#include "studio_catalog_adapter.h"
#include "studio_usb_session.h"
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
#include "studio_asset_layout.h"
#endif
#include <cJSON.h>
#include <mbedtls/platform_util.h>
#include <algorithm>
#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <freertos/portmacro.h>

namespace cat = deskbot_studio_catalog;
static std::map<std::string, std::vector<uint8_t>> files;
static std::map<std::string, emoji_data_t> emojis;
static std::map<std::string, icon_data_t> icons;
static std::vector<uint8_t> image;
static std::string fault;
static bool fail_alloc = false, block_asset = false, entered = false, resume_asset = false;
static std::mutex block_mutex;
static std::condition_variable condition;
static std::atomic<int> allocations{0}, reads{0};
static esp_partition_t partition;
static int handle_value;

static std::vector<uint8_t> Bytes(const std::filesystem::path& path) {
    std::ifstream file(path, std::ios::binary);
    return {std::istreambuf_iterator<char>(file), std::istreambuf_iterator<char>()};
}
void* heap_caps_malloc(size_t size, unsigned caps) {
    assert(!catalog_critical && caps == 3);
#if !CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    assert(size == sizeof(cat::Snapshot));
#endif
    if (fail_alloc) return nullptr;
    auto* p = std::malloc(size); if (p) ++allocations; return p;
}
void heap_caps_free(void* p) { assert(!catalog_critical); if (p) --allocations; std::free(p); }
int64_t esp_timer_get_time() {
    assert(!catalog_critical);
    return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
void catalog_log(const char*, const char*, ...) { assert(!catalog_critical); }
extern "C" void mbedtls_platform_zeroize(void* p, size_t n) {
    volatile auto* bytes = static_cast<volatile unsigned char*>(p);
    while (n--) *bytes++ = 0;
}
esp_err_t esp_partition_read(const esp_partition_t*, size_t offset, void* out, size_t size) {
    assert(!catalog_critical);
    if (fault == "partition_read" || offset > image.size() || size > image.size() - offset) return -1;
    std::memcpy(out, image.data() + offset, size); return ESP_OK;
}
esp_err_t emote_get_asset_data_by_name(emote_handle_t, const char* name, const uint8_t** data, size_t* size) {
    assert(!catalog_critical);
    if (block_asset && !std::strcmp(name, "index.json")) {
        std::unique_lock<std::mutex> lock(block_mutex); entered = true; condition.notify_all();
        condition.wait(lock, [] { return resume_asset; });
    }
    const auto row = files.find(name);
    if (row == files.end() || (fault == "missing_asset" && std::strcmp(name, "index.json"))) return -1;
    *data = row->second.data(); *size = row->second.size(); return ESP_OK;
}
esp_err_t emote_get_icon_data_by_name(emote_handle_t, const char* name, icon_data_t** row) {
    assert(!catalog_critical); const auto found = icons.find(name);
    if (found == icons.end() || fault == "missing_icon") return -1;
    *row = &found->second; return ESP_OK;
}
esp_err_t emote_get_emoji_data_by_name(emote_handle_t, const char* name, emoji_data_t** row) {
    assert(!catalog_critical); const auto found = emojis.find(name);
    if (found == emojis.end() || fault == "missing_loaded") return -1;
    *row = &found->second; return ESP_OK;
}
static const char* Text(const cJSON* row, const char* key) {
    auto* value = cJSON_GetObjectItemCaseSensitive(row, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}
static void Load(const char* folder) {
    for (const auto& entry : std::filesystem::directory_iterator(folder)) {
        if (entry.is_regular_file()) files.emplace(entry.path().filename().string(), Bytes(entry.path()));
    }
    image = files.at("package.bin"); partition.size = image.size();
    const auto& raw = files.at("index.json");
    auto* root = cJSON_ParseWithLength(reinterpret_cast<const char*>(raw.data()), raw.size());
    if (!root) return;
    auto* collection = cJSON_GetObjectItemCaseSensitive(root, "emoji_collection");
    for (const auto* row = collection ? collection->child : nullptr; row; row = row->next) {
        const auto found = files.find(Text(row, "file")); if (found == files.end()) continue;
        const auto* eaf = cJSON_GetObjectItemCaseSensitive(row, "eaf");
        const auto* fps = cJSON_GetObjectItemCaseSensitive(eaf, "fps");
        const auto* loop = cJSON_GetObjectItemCaseSensitive(eaf, "loop");
        emojis.emplace(Text(row, "name"), emoji_data_t{found->second.data(), found->second.size(),
            static_cast<uint8_t>(fps ? fps->valueint : 0), cJSON_IsTrue(loop) != 0});
    }
    collection = cJSON_GetObjectItemCaseSensitive(root, "icon_collection");
    for (const auto* row = collection ? collection->child : nullptr; row; row = row->next) {
        const auto found = files.find(Text(row, "file")); if (found == files.end()) continue;
        icons.emplace(Text(row, "name"), icon_data_t{found->second.data(), found->second.size()});
    }
    cJSON_Delete(root);
    for (auto& [name, row] : emojis) {
        (void)name;
        if (fault == "loaded_fps") ++row.fps;
        if (fault == "loaded_size") ++row.size;
        if (fault == "loaded_pointer") row.data = static_cast<const uint8_t*>(row.data) + 1;
        if (fault == "loaded_loop") row.loop = !row.loop;
    }
    if (fault == "checksum") image.back() ^= 1;
    if (fault == "partition_size") partition.size = 11;
    if (fault == "oversize_partition") partition.size = 8 * 1024 * 1024 + 1;
    fail_alloc = fault == "allocation";
}
static void Build() {
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
    deskbot_asset_layout::Configure(&handle_value);
#endif
    cat::BuildFromMounted(&partition, &handle_value);
}
static bool Provider(uint16_t cursor, cat::Page& page) { ++reads; return cat::ReadPage(cursor, page); }
static void Pages() {
    cat::Page page;
    if (!cat::ReadPage(0, page)) { std::cout << "null\n"; return; }
    for (uint16_t cursor = 0; cursor < page.count; cursor += cat::kPageSize) {
        assert(cat::ReadPage(cursor, page));
        char out[cat::kReplyBytes];
        assert(cat::Format(page, ("catalog-" + std::to_string(cursor)).c_str(),
            "device-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "boot-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "session-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1", cursor / 2 + 1, cursor * 1000, out, sizeof(out)));
        std::cout << out << '\n';
    }
}
static void Trace(const char* path) {
    const auto raw = Bytes(path); size_t pos = 0, capacity = 2048;
    deskbot_studio_session::Endpoint endpoint;
    assert(endpoint.Initialize("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"));
    endpoint.SetCatalogProvider(Provider);
    auto number = [&](unsigned n) { assert(pos + n <= raw.size()); uint64_t v = 0;
        for (unsigned i = 0; i < n; ++i) v |= static_cast<uint64_t>(raw[pos++]) << (8 * i);
        return v; };
    char output[2049]; output[2048] = 'z';
    while (pos < raw.size()) {
        auto op = raw[pos++];
        if (op == 'X') { endpoint.Disconnect(); continue; }
        if (op == 'I') { cat::Invalidate(); continue; }
        if (op == 'Q') { endpoint.SetCatalogProvider(number(1) ? Provider : nullptr); continue; }
        if (op == 'C') { capacity = number(4); assert(capacity <= 2048); continue; }
        const auto now = number(8);
        if (op == 'T') { endpoint.Tick(now); continue; }
        assert(op == 'D'); const auto size = number(4); assert(size <= raw.size() - pos);
        for (uint64_t i = 0; i < size; ++i)
            if (endpoint.Feed(raw[pos++], now, output, capacity)) std::cout << output << '\n';
        assert(output[2048] == 'z');
    }
    std::cerr << reads;
}
static void AdapterTest() {
    cat::Page page;
    assert(cat::ReadPage(0, page)); assert(!cat::ReadPage(1, page)); assert(!cat::ReadPage(128, page));
    const auto expected = std::string(page.catalog);
    cat::Invalidate(); assert(allocations == 0 && !cat::ReadPage(0, page));
    fail_alloc = true; Build(); assert(!cat::ReadPage(0, page) && allocations == 0); fail_alloc = false;
    // An invalidation while the owner builds prevents publishing a stale candidate.
    block_asset = true; std::thread owner(Build);
    { std::unique_lock<std::mutex> lock(block_mutex); condition.wait(lock, [] { return entered; }); }
    cat::Invalidate();
    { std::lock_guard<std::mutex> lock(block_mutex); resume_asset = true; } condition.notify_all(); owner.join();
    block_asset = false; assert(!cat::ReadPage(0, page) && allocations == 0);
    std::atomic<bool> stop{false}, failed{false};
    std::thread reader([&] { while (!stop) { cat::Page copy;
        if (cat::ReadPage(0, copy) && (std::string(copy.catalog) != expected || !copy.rows[0][0] || copy.count < 1)) failed = true;
    } });
    for (unsigned i = 0; i < 15; ++i) { Build(); cat::Invalidate(); }
    stop = true; reader.join(); assert(!failed && allocations == 0);
    Build(); assert(cat::ReadPage(0, page) && std::string(page.catalog) == expected);
}
int main(int argc, char** argv) {
    if (argc < 3) return 1;
    if (argc >= 4) fault = argv[3];
    Load(argv[2]); Build();
    const std::string mode = argv[1];
    if (mode == "pages") Pages();
    else if (mode == "adapter") AdapterTest();
    else if (mode == "trace" && argc == 5) Trace(argv[4]);
    else return 2;
    cat::Invalidate();
#if CONFIG_DESKBOT_ASSET_LAYOUT_MANIFEST_ENABLE
    deskbot_asset_layout::Reset(&handle_value);
#endif
    assert(allocations == 0); return 0;
}
