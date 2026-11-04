// Included once by dev_tools.cc in the isolated E1.43b diagnostic build.
#pragma once
#include <cinttypes>
#ifndef DESKBOT_MMAP_PROBE_TESTING
#include "application.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mbedtls/sha256.h>
#endif

namespace deskbot_continuous_mmap {
namespace {
constexpr uint32_t kStart = 0x01000000;
constexpr uint32_t kBytes = 0x00800000;
constexpr uint32_t kBlockBytes = 1024 * 1024;
constexpr uint32_t kChunk = 1024;
constexpr uint32_t kHoldMs = 180000;
constexpr uint32_t kIntervalMs = 10000;
constexpr uint32_t kPageReserve = 16;
constexpr size_t kInternalFreeFloor = 16384;
constexpr size_t kInternalLargestFloor = 8192;
constexpr size_t kSpiramFreeFloor = 1048576;
constexpr size_t kSpiramLargestFloor = 262144;
constexpr size_t kStackFloor = 1024;

class Digest {
public:
    Digest() {
        mbedtls_sha256_init(&context_);
        okay_ = mbedtls_sha256_starts(&context_, 0) == 0;
    }
    ~Digest() { mbedtls_sha256_free(&context_); }
    bool Update(const uint8_t* bytes, size_t count) {
        okay_ = okay_ && mbedtls_sha256_update(&context_, bytes, count) == 0;
        return okay_;
    }
    bool Finish(char (&hex)[65]) {
        uint8_t bytes[32]{};
        okay_ = okay_ && mbedtls_sha256_finish(&context_, bytes) == 0;
        Encode(bytes, sizeof(bytes), hex);
        return okay_;
    }
    static void Encode(const uint8_t* bytes, size_t count, char* hex) {
        constexpr char digits[] = "0123456789abcdef";
        for (size_t i = 0; i < count; ++i) {
            hex[2 * i] = digits[bytes[i] >> 4];
            hex[2 * i + 1] = digits[bytes[i] & 15];
        }
        hex[count * 2] = '\0';
    }
private:
    mbedtls_sha256_context context_{};
    bool okay_ = false;
};

class Mapping {
public:
    void Open() {
        status = spi_flash_mmap(kStart, kBytes, SPI_FLASH_MMAP_DATA, &data, &handle_);
        acquired = status == ESP_OK;
    }
    ~Mapping() { Release(); }
    Mapping() = default;
    Mapping(const Mapping&) = delete;
    Mapping& operator=(const Mapping&) = delete;
    void Release() {
        if (acquired && !released) {
            spi_flash_munmap(handle_);
            released = true;
            data = nullptr;
        }
    }
    const void* data = nullptr;
    esp_err_t status = ESP_FAIL;
    bool acquired = false;
    bool released = false;
private:
    spi_flash_mmap_handle_t handle_ = 0;
};

bool Emit(const char* kind, const char* payload) {
    const size_t length = std::strlen(payload);
    if (length == 0 || length > 2048) {
        return false;
    }
    const int written = std::printf("[DESKBOT_E143B %s bytes=%u] %s\n", kind,
                                    static_cast<unsigned>(length), payload);
    return std::fflush(stdout) == 0 && written > 0;
}

bool QuietNow() {
    auto& app = Application::GetInstance();
    const auto state = app.GetDeviceState();
    return (state == kDeviceStateIdle || state == kDeviceStateWifiConfiguring)
        && app.GetAudioService().IsIdle();
}

bool MemoryOkay(const MemorySnapshot& m, size_t stack) {
    return m.internal_free >= kInternalFreeFloor && m.internal_largest >= kInternalLargestFloor
        && m.spiram_free >= kSpiramFreeFloor && m.spiram_largest >= kSpiramLargestFloor
        && stack >= kStackFloor;
}

uint32_t Elapsed(int64_t start) {
    return static_cast<uint32_t>((esp_timer_get_time() - start) / 1000);
}

bool Snapshot(const char* phase, unsigned index, int64_t started, const uint8_t* mapped,
              uint32_t hold_elapsed_ms, bool& memory_ok) {
    const MemorySnapshot m = CaptureMemorySnapshot();
    // ESP-IDF reports this high-water mark in bytes, including on ESP32-S3.
    const size_t stack = uxTaskGetStackHighWaterMark(nullptr);
    const uint32_t pages = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    memory_ok = memory_ok && MemoryOkay(m, stack);
    char head[65]{}, tail[65]{};
    if (mapped != nullptr) {
        Digest::Encode(mapped, 32, head);
        Digest::Encode(mapped + kBytes - 32, 32, tail);
    }
    auto& app = Application::GetInstance();
    char json[1024];
    const int length = std::snprintf(json, sizeof(json),
        "{\"phase\":\"%s\",\"index\":%u,\"elapsed_ms\":%" PRIu32 ",\"hold_elapsed_ms\":%" PRIu32 ","
        "\"state\":%d,\"audio_idle\":%s,\"pages\":%u,\"stack_free_bytes\":%u,"
        "\"internal_free\":%u,\"internal_largest\":%u,\"internal_minimum\":%u,"
        "\"spiram_free\":%u,\"spiram_largest\":%u,\"spiram_minimum\":%u,"
        "\"head_hex\":\"%s\",\"tail_hex\":\"%s\"}",
        phase, index, Elapsed(started), hold_elapsed_ms, static_cast<int>(app.GetDeviceState()),
        app.GetAudioService().IsIdle() ? "true" : "false", static_cast<unsigned>(pages),
        static_cast<unsigned>(stack), static_cast<unsigned>(m.internal_free),
        static_cast<unsigned>(m.internal_largest), static_cast<unsigned>(m.internal_minimum),
        static_cast<unsigned>(m.spiram_free), static_cast<unsigned>(m.spiram_largest),
        static_cast<unsigned>(m.spiram_minimum), head, tail);
    return length > 0 && static_cast<size_t>(length) < sizeof(json)
        && Emit(mapped == nullptr ? "snapshot" : "sample", json);
}

bool HashBlock(unsigned index, const uint8_t* mapped, Digest& direct_all, Digest& mapped_all) {
    Digest direct;
    Digest cached;
    uint8_t buffer[kChunk];
    const uint32_t relative = index * kBlockBytes;
    for (uint32_t offset = 0; offset < kBlockBytes; offset += kChunk) {
        if (!QuietNow() || esp_flash_read(nullptr, buffer, kStart + relative + offset, kChunk) != ESP_OK
                || std::memcmp(buffer, mapped + relative + offset, kChunk) != 0
                || !direct.Update(buffer, kChunk) || !direct_all.Update(buffer, kChunk)
                || !cached.Update(mapped + relative + offset, kChunk)
                || !mapped_all.Update(mapped + relative + offset, kChunk)) {
            return false;
        }
        if ((offset + kChunk) % 4096 == 0) {
            vTaskDelay(1);
        }
    }
    char direct_sha[65]{}, cached_sha[65]{}, json[256];
    if (!direct.Finish(direct_sha) || !cached.Finish(cached_sha)) {
        return false;
    }
    std::snprintf(json, sizeof(json),
        "{\"index\":%u,\"address\":%u,\"bytes\":%u,\"read_sha256\":\"%s\",\"mmap_sha256\":\"%s\"}",
        index, static_cast<unsigned>(kStart + relative), static_cast<unsigned>(kBlockBytes),
        direct_sha, cached_sha);
    return Emit("block", json);
}

bool ExportContinuousMapping() {
    const int64_t started = esp_timer_get_time();
    if (SystemInfo::GetFlashSize() != kExpectedPhysicalFlashBytes || !QuietNow()) {
        Emit("aborted", "{\"error\":\"capacity_or_quiet_state_rejected\",\"writes_performed\":false}");
        return false;
    }
    if (!Emit("capacity", BuildFlashCapacityReport().c_str())
            || !Emit("begin", "{\"schema\":\"deskbot.continuous_mmap.begin.v1\","
                             "\"start\":16777216,\"bytes\":8388608,\"page_bytes\":65536,"
                             "\"chunk_bytes\":1024,\"hold_ms\":180000,\"interval_ms\":10000,"
                             "\"writes_performed\":false}")) {
        return false;
    }
    const uint32_t pages_before = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    Mapping mapping;
    bool memory_ok = true;
    unsigned blocks = 0, samples = 0;
    uint32_t held_ms = 0;
    char direct_sha[65]{}, cached_sha[65]{};
    const char* error = "none";
    do {
        if (!Snapshot("before", 0, started, nullptr, 0, memory_ok)) { error = "before_export_failed"; break; }
        if (!memory_ok || pages_before < kPageReserve) {
            error = "initial_memory_or_pages_below_floor"; break;
        }
        mapping.Open();
        if (!mapping.acquired || mapping.data == nullptr) { error = "mapping_failed"; break; }
        const uint32_t pages = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
        // IDF shares the active assets mapping when this physical range is
        // already mapped. A fresh mapping consumes 128 pages; sharing uses 0.
        if (pages > pages_before || (pages != pages_before
                && pages_before - pages != kBytes / SPI_FLASH_MMU_PAGE_SIZE)) {
            error = "mapped_page_count_changed"; break;
        }
        // Mapping cost is observed before any block hashing or interaction.
        if (!Snapshot("mapped", 0, started, nullptr, 0, memory_ok) || !memory_ok) {
            error = "mapped_memory_or_export_failed"; break;
        }
        const auto* pointer = static_cast<const uint8_t*>(mapping.data);
        Digest direct_all;
        Digest mapped_all;
        for (; blocks < kBytes / kBlockBytes; ++blocks) {
            if (!HashBlock(blocks, pointer, direct_all, mapped_all)) { break; }
        }
        if (blocks != kBytes / kBlockBytes || !direct_all.Finish(direct_sha)
                || !mapped_all.Finish(cached_sha) || std::strcmp(direct_sha, cached_sha) != 0) {
            error = "hash_read_or_quiet_state_failed"; break;
        }
        const int64_t hold_started = esp_timer_get_time();
        for (unsigned index = 0; index <= kHoldMs / kIntervalMs; ++index) {
            while (Elapsed(hold_started) < index * kIntervalMs) {
                vTaskDelay(pdMS_TO_TICKS(100));
            }
            held_ms = Elapsed(hold_started);
            if (held_ms > kHoldMs + 30000
                    || !Snapshot("hold", index, started, pointer, held_ms, memory_ok)) {
                error = "hold_deadline_or_export_failed"; break;
            }
            ++samples;
            if (!memory_ok || spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA) < kPageReserve) {
                error = "hold_memory_or_pages_below_floor"; break;
            }
        }
    } while (false);
    // Every path after Open reaches this release before any task deletion.
    mapping.Release();
    const uint32_t pages_after = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    if (!Snapshot("after", 0, started, nullptr, held_ms, memory_ok)) {
        return false;
    }
    const bool passed = std::strcmp(error, "none") == 0 && blocks == 8 && samples == 19
        && held_ms >= kHoldMs && mapping.released && pages_after == pages_before && memory_ok;
    char json[768];
    std::snprintf(json, sizeof(json),
        "{\"schema\":\"deskbot.continuous_mmap.summary.v1\",\"error\":\"%s\","
        "\"mapping_status\":\"%s\",\"mapping_acquired\":%s,\"mapping_released\":%s,"
        "\"blocks\":%u,\"samples\":%u,\"held_ms\":%" PRIu32 ",\"pages_before\":%u,\"pages_after\":%u,"
        "\"read_sha256\":\"%s\",\"mmap_sha256\":\"%s\",\"memory_ok\":%s,"
        "\"writes_performed\":false,\"probe_passed\":%s}",
        error, esp_err_to_name(mapping.status), mapping.acquired ? "true" : "false",
        mapping.released ? "true" : "false", blocks, samples, held_ms,
        static_cast<unsigned>(pages_before), static_cast<unsigned>(pages_after), direct_sha, cached_sha,
        memory_ok ? "true" : "false", passed ? "true" : "false");
    return Emit("summary", json) && passed;
}

void RunContinuousMappingOnce(void*) {
    auto& app = Application::GetInstance();
    DeviceState previous = kDeviceStateUnknown;
    unsigned stable = 0, quiet = 0;
    for (unsigned seconds = 0; seconds < 120; ++seconds) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        const auto state = app.GetDeviceState();
        stable = state == previous ? stable + 1 : 1;
        previous = state;
        const bool eligible = state == kDeviceStateIdle
            || (state == kDeviceStateWifiConfiguring && stable >= 10);
        quiet = eligible && app.GetAudioService().IsIdle() ? quiet + 1 : 0;
        if (quiet == 3) {
            const bool passed = ExportContinuousMapping();
            ESP_LOGI(TAG, "E1.43b mapping diagnostic %s; task exits", passed ? "passed" : "failed");
            vTaskDelete(nullptr);
            return;
        }
    }
    Emit("aborted", "{\"error\":\"quiet_state_timeout\",\"writes_performed\":false}");
    vTaskDelete(nullptr);
}
}  // namespace

void Start() {
    if (xTaskCreate(RunContinuousMappingOnce, "mmap_diag_once", 8192, nullptr, 1, nullptr) != pdPASS) {
        ESP_LOGE(TAG, "E1.43b task allocation failed");
    }
}
}  // namespace deskbot_continuous_mmap
