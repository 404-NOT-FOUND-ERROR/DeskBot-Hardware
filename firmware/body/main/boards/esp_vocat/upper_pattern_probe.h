// Internal implementation included once by dev_tools.cc, only in diagnostic builds.
#pragma once

#include "application.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mbedtls/sha256.h>

namespace deskbot_upper_pattern {
namespace {
constexpr uint32_t kStart = 0x01000000;
constexpr uint32_t kBytes = 65536;
constexpr uint32_t kSector = 4096;
constexpr uint32_t kChunk = 1024;
constexpr uint32_t kBoundaryAddress = kStart - 16;
constexpr char kExpectedPageSha[] = "63cd7bb2d9acd763bec3d74f3561ac68572f3f45fdfc8c4fc6513d7f6048df22";

// The hash API is streaming; no 64 KiB copy or heap buffer is allocated.
class Digest {
public:
    Digest() {
        mbedtls_sha256_init(&context_);
        okay_ = mbedtls_sha256_starts(&context_, 0) == 0;
    }
    ~Digest() { mbedtls_sha256_free(&context_); }
    bool Update(const uint8_t* bytes, size_t length) {
        okay_ = okay_ && mbedtls_sha256_update(&context_, bytes, length) == 0;
        return okay_;
    }
    bool Finish(char (&hex)[65]) {
        uint8_t bytes[32]{};
        okay_ = okay_ && mbedtls_sha256_finish(&context_, bytes) == 0;
        Encode(bytes, sizeof(bytes), hex);
        return okay_;
    }
    static void Encode(const uint8_t* bytes, size_t length, char* hex) {
        constexpr char digits[] = "0123456789abcdef";
        for (size_t i = 0; i < length; ++i) {
            hex[2 * i] = digits[bytes[i] >> 4];
            hex[2 * i + 1] = digits[bytes[i] & 15];
        }
        hex[length * 2] = '\0';
    }
private:
    mbedtls_sha256_context context_{};
    bool okay_ = false;
};

class Mapping {
public:
    Mapping(uint32_t address, uint32_t bytes) {
        status = spi_flash_mmap(address, bytes, SPI_FLASH_MMAP_DATA, &data, &handle_);
    }
    ~Mapping() { Release(); }
    void Release() {
        if (status == ESP_OK && !released) {
            spi_flash_munmap(handle_);
            released = true;
            data = nullptr;
        }
    }
    const void* data = nullptr;
    esp_err_t status = ESP_FAIL;
    bool released = false;
private:
    spi_flash_mmap_handle_t handle_ = 0;
};

bool Emit(const char* kind, const char* payload) {
    const size_t length = std::strlen(payload);
    if (length == 0 || length > 4096) {
        return false;
    }
    std::printf("[DESKBOT_E143A %s bytes=%u] %s\n", kind,
                static_cast<unsigned>(length), payload);
    std::fflush(stdout);
    return true;
}

bool QuietNow() {
    auto& app = Application::GetInstance();
    const auto state = app.GetDeviceState();
    return (state == kDeviceStateIdle || state == kDeviceStateWifiConfiguring)
        && app.GetAudioService().IsIdle();
}

void Abort(const char* reason) {
    char json[160];
    std::snprintf(json, sizeof(json), "{\"error\":\"%s\",\"writes_performed\":false}", reason);
    Emit("aborted", json);
}

bool HashLowAlias(char (&hex)[65], char (&protected_hex)[65]) {
    Digest hash;
    Digest protected_hash;
    uint8_t buffer[kChunk];
    for (uint32_t offset = 0; offset < kBytes; offset += kChunk) {
        if (!QuietNow() || esp_flash_read(nullptr, buffer, offset, sizeof(buffer)) != ESP_OK
                || !hash.Update(buffer, sizeof(buffer))
                || (offset < 0x9000 && !protected_hash.Update(buffer, sizeof(buffer)))) {
            return false;
        }
        if ((offset + kChunk) % kSector == 0) {
            vTaskDelay(1);
        }
    }
    return hash.Finish(hex) && protected_hash.Finish(protected_hex);
}

bool HashSector(unsigned index, const uint8_t* mapped, Digest& whole_read, Digest& whole_mapped) {
    Digest direct;
    Digest cached;
    uint8_t buffer[kChunk];
    uint8_t direct_header[64]{};
    uint8_t mapped_header[64]{};
    const uint32_t relative = index * kSector;
    for (uint32_t offset = 0; offset < kSector; offset += kChunk) {
        if (!QuietNow() || esp_flash_read(nullptr, buffer, kStart + relative + offset, sizeof(buffer)) != ESP_OK
                || !direct.Update(buffer, sizeof(buffer)) || !whole_read.Update(buffer, sizeof(buffer))
                || !cached.Update(mapped + relative + offset, sizeof(buffer))
                || !whole_mapped.Update(mapped + relative + offset, sizeof(buffer))) {
            return false;
        }
        if (offset == 0) {
            std::memcpy(direct_header, buffer, sizeof(direct_header));
            std::memcpy(mapped_header, mapped + relative, sizeof(mapped_header));
        }
    }
    char direct_sha[65], cached_sha[65], direct_hex[129], mapped_hex[129];
    if (!direct.Finish(direct_sha) || !cached.Finish(cached_sha)) {
        return false;
    }
    Digest::Encode(direct_header, sizeof(direct_header), direct_hex);
    Digest::Encode(mapped_header, sizeof(mapped_header), mapped_hex);
    char json[768];
    const int length = std::snprintf(json, sizeof(json),
        "{\"index\":%u,\"address\":%u,\"bytes\":4096,\"read_sha256\":\"%s\","
        "\"mmap_sha256\":\"%s\",\"read_header_hex\":\"%s\",\"mmap_header_hex\":\"%s\"}",
        index, static_cast<unsigned>(kStart + relative), direct_sha, cached_sha, direct_hex, mapped_hex);
    return length > 0 && static_cast<size_t>(length) < sizeof(json) && Emit("block", json);
}

bool ExportUpperPattern() {
    const size_t flash_size = SystemInfo::GetFlashSize();
    if (flash_size != kExpectedPhysicalFlashBytes || !QuietNow()) {
        Abort("capacity_or_quiet_state_rejected");
        return false;
    }
    // The existing report lets the host validate the *current* six-partition layout.
    if (!Emit("capacity", BuildFlashCapacityReport().c_str())) {
        Abort("capacity_export_failed");
        return false;
    }
    const MemorySnapshot before = CaptureMemorySnapshot();
    const uint32_t pages_before = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    if (!Emit("begin", "{\"schema\":\"deskbot.upper_pattern.begin.v1\",\"start\":16777216,"
                       "\"bytes\":65536,\"sector_bytes\":4096,\"chunk_bytes\":1024,\"writes_performed\":false}")) {
        return false;
    }
    char alias_sha[65]{};
    char protected_sha[65]{};
    if (!HashLowAlias(alias_sha, protected_sha)) {
        Abort("low_alias_read_or_quiet_state_failed");
        return false;
    }
    Digest whole_read;
    Digest whole_mapped;
    Mapping upper(kStart, kBytes);
    if (upper.status != ESP_OK || upper.data == nullptr) {
        Abort("upper_mapping_failed");
        return false;
    }
    const uint32_t pages_upper = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    const MemorySnapshot upper_memory = CaptureMemorySnapshot();
    const auto* pointer = static_cast<const uint8_t*>(upper.data);
    for (unsigned index = 0; index < 16; ++index) {
        if (!HashSector(index, pointer, whole_read, whole_mapped)) {
            Abort("sector_hash_read_or_quiet_state_failed");
            return false;  // Mapping destructor also covers this path.
        }
        vTaskDelay(1);
    }
    char read_sha[65]{}, mapped_sha[65]{};
    const bool digest_ok = whole_read.Finish(read_sha) && whole_mapped.Finish(mapped_sha);
    upper.Release();
    const uint32_t pages_after_upper = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    if (!digest_ok || !QuietNow()) {
        Abort("page_hash_or_quiet_state_failed");
        return false;
    }
    std::array<uint8_t, 32> boundary_read{};
    std::array<uint8_t, 32> boundary_mapped{};
    const esp_err_t boundary_read_status = esp_flash_read(nullptr, boundary_read.data(), kBoundaryAddress, 32);
    Mapping boundary(kStart - SPI_FLASH_MMU_PAGE_SIZE, SPI_FLASH_MMU_PAGE_SIZE * 2);
    const uint32_t pages_boundary = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    const MemorySnapshot boundary_memory = CaptureMemorySnapshot();
    const bool boundary_ok = boundary.status == ESP_OK && boundary.data != nullptr;
    if (boundary_ok) {
        std::memcpy(boundary_mapped.data(), static_cast<const uint8_t*>(boundary.data)
                    + SPI_FLASH_MMU_PAGE_SIZE - 16, boundary_mapped.size());
    }
    boundary.Release();
    const uint32_t pages_after_boundary = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    const MemorySnapshot after = CaptureMemorySnapshot();
    char boundary_read_hex[65], boundary_mmap_hex[65];
    Digest::Encode(boundary_read.data(), boundary_read.size(), boundary_read_hex);
    Digest::Encode(boundary_mapped.data(), boundary_mapped.size(), boundary_mmap_hex);
    const bool passed = std::strcmp(read_sha, kExpectedPageSha) == 0
        && std::strcmp(mapped_sha, kExpectedPageSha) == 0
        && std::strcmp(alias_sha, read_sha) != 0
        && boundary_read_status == ESP_OK && boundary_ok && boundary_read == boundary_mapped
        && upper.released && boundary.released && pages_after_upper == pages_before
        && pages_after_boundary == pages_before && QuietNow();
    cJSON* report = cJSON_CreateObject();
    if (report == nullptr) {
        Abort("summary_allocation_failed");
        return false;
    }
    const bool complete = cJSON_AddStringToObject(report, "schema", "deskbot.upper_pattern.summary.v1")
        && cJSON_AddStringToObject(report, "read_sha256", read_sha)
        && cJSON_AddStringToObject(report, "mmap_sha256", mapped_sha)
        && cJSON_AddStringToObject(report, "low_alias_read_sha256", alias_sha)
        && cJSON_AddStringToObject(report, "low_protected_read_sha256", protected_sha)
        && cJSON_AddNumberToObject(report, "boundary_address", kBoundaryAddress)
        && cJSON_AddStringToObject(report, "boundary_read_hex", boundary_read_hex)
        && cJSON_AddStringToObject(report, "boundary_mmap_hex", boundary_mmap_hex)
        && cJSON_AddStringToObject(report, "boundary_read_status", esp_err_to_name(boundary_read_status))
        && cJSON_AddStringToObject(report, "boundary_mmap_status", esp_err_to_name(boundary.status))
        && cJSON_AddNumberToObject(report, "pages_before", pages_before)
        && cJSON_AddNumberToObject(report, "pages_while_upper", pages_upper)
        && cJSON_AddNumberToObject(report, "pages_after_upper", pages_after_upper)
        && cJSON_AddNumberToObject(report, "pages_while_boundary", pages_boundary)
        && cJSON_AddNumberToObject(report, "pages_after_boundary", pages_after_boundary)
        && cJSON_AddBoolToObject(report, "upper_released", upper.released)
        && cJSON_AddBoolToObject(report, "boundary_released", boundary.released)
        && cJSON_AddBoolToObject(report, "writes_performed", false)
        && cJSON_AddBoolToObject(report, "probe_passed", passed)
        && AddMemorySnapshot(report, "before", before)
        && AddMemorySnapshot(report, "upper_mapped", upper_memory)
        && AddMemorySnapshot(report, "boundary_mapped", boundary_memory)
        && AddMemorySnapshot(report, "after", after);
    char* json = complete ? cJSON_PrintUnformatted(report) : nullptr;
    const bool exported = json != nullptr && Emit("summary", json);
    cJSON_free(json);
    cJSON_Delete(report);
    if (!exported) {
        Abort("summary_export_failed");
    }
    return exported && passed;
}

void RunUpperPatternOnce(void*) {
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
            const bool passed = ExportUpperPattern();
            ESP_LOGI(TAG, "E1.43a read-only export %s; task exits", passed ? "passed" : "failed");
            vTaskDelete(nullptr);
            return;
        }
    }
    Abort("quiet_state_timeout");
    vTaskDelete(nullptr);
}
}  // namespace

void Start() {
    if (xTaskCreate(RunUpperPatternOnce, "upper_diag_once", 8192, nullptr, 1, nullptr) != pdPASS) {
        ESP_LOGE(TAG, "E1.43a diagnostic task allocation failed");
    }
}
}  // namespace deskbot_upper_pattern
