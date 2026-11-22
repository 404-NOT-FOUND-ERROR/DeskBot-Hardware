#include "sdkconfig.h"
#if CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE
#include "studio_catalog_adapter.h"
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
#include "studio_asset_layout.h"
#endif
#include <esp_heap_caps.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>
#include <mbedtls/sha256.h>
#include <new>

namespace deskbot_studio_catalog {
namespace {
Snapshot* installed = nullptr;
uint32_t generation = 0;
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
struct Guard {
    Guard() { portENTER_CRITICAL(&mux); }
    ~Guard() { portEXIT_CRITICAL(&mux); }
};
void Release(Snapshot* snapshot) { if (snapshot) { snapshot->~Snapshot(); heap_caps_free(snapshot); } }
bool Begin(void* context) { return mbedtls_sha256_starts(static_cast<mbedtls_sha256_context*>(context), 0) == 0; }
bool Update(void* context, const void* data, size_t size) {
    return mbedtls_sha256_update(static_cast<mbedtls_sha256_context*>(context), static_cast<const uint8_t*>(data), size) == 0;
}
bool Finish(void* context, char* output) {
    uint8_t bytes[32];
    if (mbedtls_sha256_finish(static_cast<mbedtls_sha256_context*>(context), bytes)) return false;
    constexpr char digits[] = "0123456789abcdef";
    for (unsigned i = 0; i < 32; ++i) { output[i * 2] = digits[bytes[i] >> 4]; output[i * 2 + 1] = digits[bytes[i] & 15]; }
    output[64] = 0;
    return true;
}
bool Asset(void* context, const char* name, const uint8_t*& data, size_t& size) {
    return emote_get_asset_data_by_name(static_cast<emote_handle_t>(context), name, &data, &size) == ESP_OK;
}
bool Parsed(void* context, const char* id, const uint8_t* data, size_t size, unsigned fps, bool loop, bool status) {
    auto handle = static_cast<emote_handle_t>(context);
    if (status) {
        icon_data_t* row = nullptr;
        return emote_get_icon_data_by_name(handle, id, &row) == ESP_OK && row && row->data == data && row->size == size;
    }
    emoji_data_t* row = nullptr;
    return emote_get_emoji_data_by_name(handle, id, &row) == ESP_OK && row && row->data == data &&
        row->size == size && row->fps == fps && row->loop == loop;
}
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
bool FullFrame(void* context, const uint8_t* data, size_t size) {
    return deskbot_asset_layout::FullFrame(static_cast<emote_handle_t>(context), data, size);
}
#endif
bool PackageDigest(const esp_partition_t* partition, Digest hash, char* output) {
    uint8_t buffer[1024];
    if (!partition || partition->size < 12 || partition->size > 8 * 1024 * 1024 ||
        esp_partition_read(partition, 0, buffer, 12) != ESP_OK) return false;
    const auto word = [&](unsigned offset) {
        uint32_t value = 0;
        for (unsigned i = 0; i < 4; ++i) value |= static_cast<uint32_t>(buffer[offset + i]) << (i * 8);
        return value;
    };
    const uint32_t count = word(0), checksum = word(4), payload = word(8);
    if (!count || count > 256 || checksum > 0xffff || payload > partition->size - 12 || payload < count * 44 || !hash.start(hash.context)) return false;
    const size_t end = payload + 12;
    uint32_t calculated = 0;
    for (size_t offset = 0; offset < end;) {
        const size_t bytes = end - offset < sizeof(buffer) ? end - offset : sizeof(buffer);
        if (esp_partition_read(partition, offset, buffer, bytes) != ESP_OK || !hash.update(hash.context, buffer, bytes)) return false;
        for (size_t i = offset < 12 ? 12 - offset : 0; i < bytes; ++i) calculated += buffer[i];
        offset += bytes;
    }
    return (calculated & 0xffff) == checksum && hash.finish(hash.context, output);
}
}  // namespace

void Invalidate() {
    Snapshot* old;
    { Guard guard; old = installed; installed = nullptr; ++generation; }
    Release(old);
}

void BuildFromMounted(const esp_partition_t* partition, emote_handle_t handle) {
    Invalidate();
    uint32_t ticket;
    { Guard guard; ticket = generation; }
    const auto started = esp_timer_get_time();
    void* memory = heap_caps_malloc(sizeof(Snapshot), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!memory) { ESP_LOGW("DeskBotCatalog", "metadata allocation unavailable"); return; }
    auto* candidate = new (memory) Snapshot{};
    mbedtls_sha256_context context;
    mbedtls_sha256_init(&context);
    const Digest hash{&context, Begin, Update, Finish};
    const uint8_t* index = nullptr;
    size_t size = 0;
    char package[65];
#if CONFIG_DESKBOT_ASSET_LAYOUT_ENABLE
    Source source{handle, Asset, Parsed};
    source.full_frame = FullFrame;
    const bool valid = handle && Asset(handle, "index.json", index, size) && size <= kMaxIndexBytes &&
        PackageDigest(partition, hash, package) && Build(*candidate, index, size, package, source, hash);
#else
    const bool valid = handle && Asset(handle, "index.json", index, size) && size <= kMaxIndexBytes &&
        PackageDigest(partition, hash, package) && Build(*candidate, index, size, package, {handle, Asset, Parsed}, hash);
#endif
    mbedtls_sha256_free(&context);
    bool published = false;
    if (valid) {
        Guard guard;
        if (ticket == generation && !installed) { installed = candidate; published = true; }
    }
    // Hashes, allocation, logs and frees stay outside the metadata copy lock.
    if (!published) Release(candidate);
    ESP_LOGI("DeskBotCatalog", "directory %s; build_ms=%u; snapshot_bytes=%u",
        published ? "ready" : "unavailable", static_cast<unsigned>((esp_timer_get_time() - started) / 1000),
        static_cast<unsigned>(sizeof(Snapshot)));
}

bool ReadPage(uint16_t cursor, Page& page) {
    Guard guard;
    if (!installed || cursor >= installed->count || cursor % kPageSize) return false;
    page.count = installed->count;
    page.cursor = cursor;
    std::memcpy(page.catalog, installed->catalog, sizeof(page.catalog));
    std::memcpy(page.package, installed->package, sizeof(page.package));
    std::memcpy(page.index, installed->index, sizeof(page.index));
    for (unsigned i = 0; i < kPageSize; ++i) {
        if (cursor + i < installed->count) std::memcpy(page.rows[i], installed->entries[cursor + i].json, kEntryBytes);
        else page.rows[i][0] = 0;
    }
    return true;
}
}  // namespace deskbot_studio_catalog
#endif
