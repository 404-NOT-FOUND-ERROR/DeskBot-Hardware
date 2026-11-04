// Execute the production probe with deterministic host Flash/RTOS/hash stubs.
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#define DESKBOT_MMAP_PROBE_TESTING 1
#define TAG "test"
template <typename... Args> void FakeLog(const char*, const char*, Args...) {}
#define ESP_LOGI(...) FakeLog(__VA_ARGS__)
#define ESP_LOGE(...) FakeLog(__VA_ARGS__)
using esp_err_t = int;
using spi_flash_mmap_handle_t = unsigned;
constexpr int ESP_OK = 0, ESP_FAIL = -1, pdPASS = 1;
constexpr unsigned SPI_FLASH_MMAP_DATA = 0, SPI_FLASH_MMU_PAGE_SIZE = 65536;
constexpr size_t kExpectedPhysicalFlashBytes = 32 * 1024 * 1024;
enum DeviceState { kDeviceStateUnknown, kDeviceStateIdle, kDeviceStateWifiConfiguring, kDeviceStateSpeaking };
struct MemorySnapshot {
    size_t internal_free, internal_largest, internal_minimum;
    size_t spiram_free, spiram_largest, spiram_minimum;
};

namespace fake {
std::string fault;
std::vector<uint8_t> flash(8 * 1024 * 1024);
int64_t now = 0;
unsigned opened = 0, closed = 0, reads = 0, deleted = 0, created = 0;
bool mapped = false;
void Reset(const char* name) {
    fault = name;
    for (size_t i = 0; i < flash.size(); ++i) { flash[i] = static_cast<uint8_t>(i * 17 + i / 4096); }
    now = 0; opened = closed = reads = deleted = created = 0; mapped = false;
}
bool Holding() { return mapped && reads == 8192; }
}

const char* esp_err_to_name(int code) { return code == ESP_OK ? "ESP_OK" : "ESP_FAIL"; }
int64_t esp_timer_get_time() { return fake::now * 1000; }
unsigned pdMS_TO_TICKS(unsigned n) { return n; }
void vTaskDelay(unsigned n) {
    fake::now += n;
    if (fake::fault == "deadline" && fake::Holding()) { fake::now += 250000; }
}
void vTaskDelete(void*) { assert(!fake::mapped); ++fake::deleted; }
int xTaskCreate(void (*)(void*), const char*, unsigned stack, void*, unsigned priority, void*) {
    assert(stack == 8192 && priority == 1);
    ++fake::created;
    return fake::fault == "task" ? 0 : pdPASS;
}
unsigned uxTaskGetStackHighWaterMark(void*) { return fake::fault == "stack" ? 512 : 4096; }
unsigned spi_flash_mmap_get_free_pages(unsigned) {
    if (fake::fault == "few_pages") { return 15; }
    if (fake::fault == "shared") { return 61; }
    if (fake::fault == "page_drift" && fake::mapped) { return 62; }
    if (fake::fault == "page_after" && fake::closed) { return 188; }
    if (fake::fault == "hold_pages" && fake::Holding()) { return 1; }
    return fake::mapped ? 61 : 189;
}
int spi_flash_mmap(uint32_t start, uint32_t bytes, unsigned, const void** pointer, unsigned* handle) {
    assert(start == 0x1000000 && bytes == 0x800000 && !fake::mapped);
    ++fake::opened;
    if (fake::fault == "map") { return ESP_FAIL; }
    fake::mapped = true;
    *pointer = fake::fault == "null" ? nullptr : fake::flash.data();
    *handle = 97;
    return ESP_OK;
}
void spi_flash_munmap(unsigned handle) {
    assert(handle == 97 && fake::mapped && fake::closed == 0);
    fake::mapped = false;
    ++fake::closed;
}
int esp_flash_read(void*, void* buffer, uint32_t offset, size_t count) {
    assert(fake::mapped && count == 1024 && offset >= 0x1000000 && offset + count <= 0x1800000);
    ++fake::reads;
    if (fake::fault == "read" && fake::reads == 5) { return ESP_FAIL; }
    std::memcpy(buffer, fake::flash.data() + offset - 0x1000000, count);
    if (fake::fault == "mismatch" && fake::reads == 8192) { static_cast<uint8_t*>(buffer)[0] ^= 1; }
    return ESP_OK;
}
MemorySnapshot CaptureMemorySnapshot() {
    MemorySnapshot value{60000, 32000, 40000, 15000000, 14000000, 14000000};
    if (fake::fault == "memory_before" || (fake::fault == "memory_mapped" && fake::mapped)
            || (fake::fault == "memory_hold" && fake::Holding())) { value.internal_free = 12000; }
    return value;
}
struct Audio {
    bool IsIdle() { return !(fake::fault == "activity" && fake::reads > 3); }
};
struct Application {
    static Application& GetInstance() { static Application app; return app; }
    DeviceState GetDeviceState() {
        return fake::fault == "not_quiet" ? kDeviceStateSpeaking : kDeviceStateIdle;
    }
    Audio& GetAudioService() { static Audio audio; return audio; }
};
struct SystemInfo {
    static size_t GetFlashSize() { return fake::fault == "capacity" ? 0x1000000 : kExpectedPhysicalFlashBytes; }
};
std::string BuildFlashCapacityReport() { return "{}"; }

// Mock the crypto API to test control flow and RAII, not cryptography.
struct mbedtls_sha256_context { uint64_t value = 0; };
void mbedtls_sha256_init(mbedtls_sha256_context* c) { c->value = 0; }
void mbedtls_sha256_free(mbedtls_sha256_context*) {}
int mbedtls_sha256_starts(mbedtls_sha256_context* c, int) { c->value = 0; return 0; }
int mbedtls_sha256_update(mbedtls_sha256_context* c, const uint8_t* data, size_t size) {
    if (fake::fault == "hash") { return -1; }
    for (size_t i = 0; i < size; ++i) { c->value = (c->value * 31) ^ data[i]; }
    return 0;
}
int mbedtls_sha256_finish(mbedtls_sha256_context* c, uint8_t* out) {
    for (unsigned i = 0; i < 32; ++i) { out[i] = static_cast<uint8_t>(c->value >> (8 * (i % 8))); }
    return fake::fault == "finish" ? -1 : 0;
}

#include "continuous_mmap_probe.h"

int main() {
    const char* cases[] = {"success", "shared", "capacity", "not_quiet", "few_pages", "memory_before", "stack",
                          "map", "null", "page_drift", "memory_mapped", "read", "mismatch", "activity",
                          "hash", "finish", "memory_hold", "hold_pages", "deadline", "page_after"};
    for (const char* name : cases) {
        fake::Reset(name);
        const bool passed = deskbot_continuous_mmap::ExportContinuousMapping();
        assert(passed == (fake::fault == "success" || fake::fault == "shared"));
        assert(!fake::mapped && fake::opened <= 1);
        assert(fake::closed == (fake::opened && fake::fault != "map" ? 1u : 0u));
        if (passed) { assert(fake::reads == 8192 && fake::now >= 180000); }
        std::printf("case_passed:%s\n", name);
    }
    fake::Reset("not_quiet");
    deskbot_continuous_mmap::RunContinuousMappingOnce(nullptr);
    assert(fake::now == 120000 && fake::deleted == 1 && fake::opened == 0);
    fake::Reset("task");
    deskbot_continuous_mmap::Start();
    assert(fake::created == 1 && !fake::mapped);
    std::puts("continuous_mmap_cpp_tests_passed:22");
}
