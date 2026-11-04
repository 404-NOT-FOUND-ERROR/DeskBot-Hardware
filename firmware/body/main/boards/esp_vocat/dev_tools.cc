#include "dev_tools.h"
#include "esp_vocat.h"
#include "vocat_base_control.h"
#include "base_event_query.h"
#include "base_magnetic_query.h"
#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
#include "expression_runtime_observer.h"
#endif
#include "audio_analysis.h"
#include "mcp_server.h"
#include "board.h"
#include "assets/lang_config.h"
#include <esp_log.h>
#include <esp_timer.h>
#include <cstdio>
#include <cstring>
#include "customer_ui/alarm_api.h"
#include "ui_bridge.h"
#include "system_info.h"
#if CONFIG_DESKBOT_STUDIO_IDENTITY_ENABLE
#include "studio_device_link.h"
#endif
#include <esp_partition.h>
#include <esp_flash.h>
#include <spi_flash_mmap.h>
#include <esp_heap_caps.h>
#include <esp_psram.h>
#include <array>
#if CONFIG_DESKBOT_FLASH_PROBE_BOOT_EXPORT
#include "application.h"
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#endif

#define TAG "DevTools"

namespace {

constexpr size_t kExpectedPhysicalFlashBytes = 32 * 1024 * 1024;
constexpr uint32_t kFlashBoundary = 16 * 1024 * 1024;
constexpr uint32_t kSampleBytes = 32;
constexpr uint32_t kLowerSampleAddress = kFlashBoundary - (kSampleBytes / 2);
constexpr uint32_t kUpperSampleAddress = kFlashBoundary;

struct MemorySnapshot {
    size_t internal_free;
    size_t internal_largest;
    size_t internal_minimum;
    size_t spiram_free;
    size_t spiram_largest;
    size_t spiram_minimum;
};

MemorySnapshot CaptureMemorySnapshot() {
    return {
        heap_caps_get_free_size(MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL),
        heap_caps_get_largest_free_block(MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL),
        heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT | MALLOC_CAP_INTERNAL),
        heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
        heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),
        heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM),
    };
}

bool AllBytesAreFF(const std::array<uint8_t, kSampleBytes>& bytes) {
    for (uint8_t byte : bytes) {
        if (byte != 0xFF) {
            return false;
        }
    }
    return true;
}

struct MmapSample {
    esp_err_t status = ESP_FAIL;
    bool compared = false;
    bool released = false;
    uint32_t pages_while_mapped = 0;
    MemorySnapshot memory_while_mapped{};
    std::array<uint8_t, kSampleBytes> bytes{};
};

MmapSample ReadMappedSample(uint32_t page_address, uint32_t mapped_bytes,
                            uint32_t sample_offset) {
    MmapSample sample;
    const void* mapped = nullptr;
    spi_flash_mmap_handle_t handle = 0;
    sample.status = spi_flash_mmap(page_address, mapped_bytes,
                                   SPI_FLASH_MMAP_DATA, &mapped, &handle);
    if (sample.status != ESP_OK) {
        return sample;
    }
    sample.pages_while_mapped = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    sample.memory_while_mapped = CaptureMemorySnapshot();
    if (mapped != nullptr && sample_offset + kSampleBytes <= mapped_bytes) {
        std::memcpy(sample.bytes.data(),
                    static_cast<const uint8_t*>(mapped) + sample_offset,
                    kSampleBytes);
        sample.compared = true;
    }
    spi_flash_munmap(handle);
    sample.released = true;
    return sample;
}

bool AddMemorySnapshot(cJSON* report, const char* prefix,
                       const MemorySnapshot& memory) {
    char key[64];
    const auto add = [report, &key, prefix](const char* suffix, size_t value) {
        std::snprintf(key, sizeof(key), "%s_%s", prefix, suffix);
        return cJSON_AddNumberToObject(report, key, static_cast<double>(value)) != nullptr;
    };
    return add("internal_free", memory.internal_free)
        && add("internal_largest", memory.internal_largest)
        && add("internal_minimum", memory.internal_minimum)
        && add("spiram_free", memory.spiram_free)
        && add("spiram_largest", memory.spiram_largest)
        && add("spiram_minimum", memory.spiram_minimum);
}

void BytesToHex(const std::array<uint8_t, kSampleBytes>& bytes,
                char (&out)[kSampleBytes * 2 + 1]) {
    static constexpr char kHexDigits[] = "0123456789abcdef";
    for (size_t i = 0; i < bytes.size(); ++i) {
        out[i * 2] = kHexDigits[bytes[i] >> 4];
        out[i * 2 + 1] = kHexDigits[bytes[i] & 0x0F];
    }
    out[kSampleBytes * 2] = '\0';
}

std::string BuildFlashAddressingProbeReport() {
    std::array<uint8_t, kSampleBytes> lower_read{};
    std::array<uint8_t, kSampleBytes> upper_read{};
    uint8_t out_of_range_byte = 0;
    const size_t flash_size = SystemInfo::GetFlashSize();
    const MemorySnapshot before = CaptureMemorySnapshot();
    const uint32_t pages_before = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    const bool flash_size_matches = flash_size == kExpectedPhysicalFlashBytes;
    const esp_err_t lower_read_status = flash_size_matches
        ? esp_flash_read(nullptr, lower_read.data(), kLowerSampleAddress, kSampleBytes)
        : ESP_ERR_INVALID_SIZE;
    const esp_err_t upper_read_status = flash_size_matches
        ? esp_flash_read(nullptr, upper_read.data(), kUpperSampleAddress, kSampleBytes)
        : ESP_ERR_INVALID_SIZE;
    const esp_err_t out_of_range_status = flash_size_matches
        && flash_size < UINT32_MAX
        ? esp_flash_read(nullptr, &out_of_range_byte,
                         static_cast<uint32_t>(flash_size), 1)
        : ESP_ERR_INVALID_SIZE;
    const uint32_t lower_page = kLowerSampleAddress
        & ~(SPI_FLASH_MMU_PAGE_SIZE - 1);
    const uint32_t lower_offset = kLowerSampleAddress - lower_page;
    const MmapSample lower_mmap = flash_size_matches
        ? ReadMappedSample(lower_page, SPI_FLASH_MMU_PAGE_SIZE * 2, lower_offset)
        : MmapSample{};
    const uint32_t pages_after_lower = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    const MmapSample upper_mmap = flash_size_matches
        ? ReadMappedSample(kUpperSampleAddress, SPI_FLASH_MMU_PAGE_SIZE, 0)
        : MmapSample{};
    const uint32_t pages_after_upper = spi_flash_mmap_get_free_pages(SPI_FLASH_MMAP_DATA);
    const MemorySnapshot after = CaptureMemorySnapshot();

    cJSON* report = cJSON_CreateObject();
    if (report == nullptr) {
        return "{\"error\":\"addressing_probe_unavailable\"}";
    }
    const bool lower_read_ok = lower_read_status == ESP_OK;
    const bool upper_read_ok = upper_read_status == ESP_OK;
    const bool lower_match = lower_mmap.compared && lower_read_ok
        && std::memcmp(lower_read.data(), lower_mmap.bytes.data(), kSampleBytes) == 0;
    const bool upper_match = upper_mmap.compared && upper_read_ok
        && std::memcmp(upper_read.data(), upper_mmap.bytes.data(), kSampleBytes) == 0;
    const bool lower_pages_restored = pages_after_lower == pages_before;
    const bool upper_pages_restored = pages_after_upper == pages_before;
    const bool pages_restored = lower_pages_restored && upper_pages_restored;
    const bool upper_all_ff = upper_read_ok && AllBytesAreFF(upper_read);
    const bool out_of_range_rejected = out_of_range_status == ESP_ERR_INVALID_ARG;
    char lower_hex[kSampleBytes * 2 + 1];
    char upper_hex[kSampleBytes * 2 + 1];
    BytesToHex(lower_read, lower_hex);
    BytesToHex(upper_read, upper_hex);
    bool complete = cJSON_AddStringToObject(
                        report, "schema", "deskbot.n32r16.addressing_probe.e1_42/v1") != nullptr
        && cJSON_AddNumberToObject(report, "flash_size", static_cast<double>(flash_size)) != nullptr
        && cJSON_AddBoolToObject(report, "flash_size_matches_expected", flash_size_matches) != nullptr
        && cJSON_AddNumberToObject(report, "boundary", kFlashBoundary) != nullptr
        && cJSON_AddNumberToObject(report, "sample_bytes", kSampleBytes) != nullptr
        && cJSON_AddNumberToObject(report, "lower_address", kLowerSampleAddress) != nullptr
        && cJSON_AddNumberToObject(report, "upper_address", kUpperSampleAddress) != nullptr
        && cJSON_AddNumberToObject(report, "lower_mmap_address", lower_page) != nullptr
        && cJSON_AddNumberToObject(report, "lower_mmap_bytes", flash_size_matches ? SPI_FLASH_MMU_PAGE_SIZE * 2 : 0) != nullptr
        && cJSON_AddNumberToObject(report, "lower_mmap_sample_offset", lower_offset) != nullptr
        && cJSON_AddNumberToObject(report, "upper_mmap_address", kUpperSampleAddress) != nullptr
        && cJSON_AddNumberToObject(report, "upper_mmap_bytes", flash_size_matches ? SPI_FLASH_MMU_PAGE_SIZE : 0) != nullptr
        && cJSON_AddNumberToObject(report, "upper_mmap_sample_offset", 0) != nullptr
        && cJSON_AddStringToObject(report, "lower_sample_hex", lower_hex) != nullptr
        && cJSON_AddStringToObject(report, "upper_sample_hex", upper_hex) != nullptr
        && cJSON_AddStringToObject(report, "lower_read_status", esp_err_to_name(lower_read_status)) != nullptr
        && cJSON_AddStringToObject(report, "upper_read_status", esp_err_to_name(upper_read_status)) != nullptr
        && cJSON_AddStringToObject(report, "out_of_range_read_status", esp_err_to_name(out_of_range_status)) != nullptr
        && cJSON_AddStringToObject(report, "lower_mmap_status", esp_err_to_name(lower_mmap.status)) != nullptr
        && cJSON_AddStringToObject(report, "upper_mmap_status", esp_err_to_name(upper_mmap.status)) != nullptr
        && cJSON_AddBoolToObject(report, "lower_read_mmap_equal", lower_match) != nullptr
        && cJSON_AddBoolToObject(report, "upper_read_mmap_equal", upper_match) != nullptr
        && cJSON_AddBoolToObject(report, "upper_read_all_ff", upper_all_ff) != nullptr
        && cJSON_AddBoolToObject(report, "out_of_range_read_rejected", out_of_range_rejected) != nullptr
        && cJSON_AddBoolToObject(report, "lower_mmap_released", lower_mmap.released) != nullptr
        && cJSON_AddBoolToObject(report, "upper_mmap_released", upper_mmap.released) != nullptr
        && cJSON_AddNumberToObject(report, "mmap_pages_before", pages_before) != nullptr
        && cJSON_AddNumberToObject(report, "mmap_pages_while_lower_mapped", lower_mmap.pages_while_mapped) != nullptr
        && cJSON_AddNumberToObject(report, "mmap_pages_after_lower", pages_after_lower) != nullptr
        && cJSON_AddNumberToObject(report, "mmap_pages_while_upper_mapped", upper_mmap.pages_while_mapped) != nullptr
        && cJSON_AddNumberToObject(report, "mmap_pages_after_upper", pages_after_upper) != nullptr
        && cJSON_AddBoolToObject(report, "lower_mmap_pages_restored", lower_pages_restored) != nullptr
        && cJSON_AddBoolToObject(report, "upper_mmap_pages_restored", upper_pages_restored) != nullptr
        && cJSON_AddBoolToObject(report, "mmap_pages_restored", pages_restored) != nullptr
        && cJSON_AddBoolToObject(report, "writes_performed", false) != nullptr
        && cJSON_AddBoolToObject(report, "probe_passed", flash_size_matches && lower_match
            && upper_match && upper_all_ff && out_of_range_rejected
            && lower_mmap.released && upper_mmap.released && pages_restored) != nullptr
        && AddMemorySnapshot(report, "before", before)
        && AddMemorySnapshot(report, "lower_mapped", lower_mmap.memory_while_mapped)
        && AddMemorySnapshot(report, "upper_mapped", upper_mmap.memory_while_mapped)
        && AddMemorySnapshot(report, "after", after);
    if (!complete) {
        cJSON_Delete(report);
        return "{\"error\":\"addressing_probe_unavailable\"}";
    }
    char* json = cJSON_PrintUnformatted(report);
    if (json == nullptr) {
        cJSON_Delete(report);
        return "{\"error\":\"addressing_probe_unavailable\"}";
    }
    std::string result(json);
    cJSON_free(json);
    cJSON_Delete(report);
    return result;
}

std::string BuildFlashCapacityReport() {
    const auto unavailable = [] {
        return std::string("{\"error\":\"capacity_report_unavailable\"}");
    };

    cJSON* report = cJSON_CreateObject();
    cJSON* partitions = cJSON_CreateArray();
    if (report == nullptr || partitions == nullptr) {
        cJSON_Delete(report);
        cJSON_Delete(partitions);
        return unavailable();
    }

    const size_t flash_size = SystemInfo::GetFlashSize();
    if (flash_size == 0
        || cJSON_AddStringToObject(report, "schema",
                                "deskbot.n32r16.capacity_query.e1_41/v1") == nullptr
        || cJSON_AddStringToObject(report, "source_tool", "self.get_flash_capacity") == nullptr
        || cJSON_AddNumberToObject(report, "flash_size", static_cast<double>(flash_size)) == nullptr
        || cJSON_AddNumberToObject(report, "expected_flash_size",
                                   static_cast<double>(kExpectedPhysicalFlashBytes)) == nullptr
        || cJSON_AddBoolToObject(report, "flash_size_matches_expected",
                                 flash_size == kExpectedPhysicalFlashBytes) == nullptr
        || cJSON_AddBoolToObject(report, "mmap_verified", false) == nullptr
        || cJSON_AddBoolToObject(report, "upper_assets_verified", false) == nullptr
        || cJSON_AddStringToObject(report, "claim_scope",
                                   "driver_reported_flash_size_and_partition_table_only") == nullptr) {
        cJSON_Delete(report);
        cJSON_Delete(partitions);
        return unavailable();
    }
    bool complete = true;
    esp_partition_iterator_t iterator = esp_partition_find(
        ESP_PARTITION_TYPE_ANY, ESP_PARTITION_SUBTYPE_ANY, nullptr);
    for (; iterator != nullptr; iterator = esp_partition_next(iterator)) {
        const esp_partition_t* partition = esp_partition_get(iterator);
        cJSON* item = cJSON_CreateObject();
        if (partition == nullptr || item == nullptr
            || cJSON_AddStringToObject(item, "label", partition->label) == nullptr
            || cJSON_AddNumberToObject(item, "address",
                                       static_cast<double>(partition->address)) == nullptr
            || cJSON_AddNumberToObject(item, "size",
                                       static_cast<double>(partition->size)) == nullptr
            || !cJSON_AddItemToArray(partitions, item)) {
            cJSON_Delete(item);
            complete = false;
            break;
        }
    }
    esp_partition_iterator_release(iterator);
    if (!complete || cJSON_GetArraySize(partitions) == 0
        || !cJSON_AddItemToObject(report, "partition_table", partitions)) {
        cJSON_Delete(report);
        cJSON_Delete(partitions);
        return unavailable();
    }

    char* json = cJSON_PrintUnformatted(report);
    if (json == nullptr) {
        cJSON_Delete(report);
        return unavailable();
    }
    std::string result(json);
    cJSON_free(json);
    cJSON_Delete(report);
    return result;
}

#if CONFIG_DESKBOT_FLASH_PROBE_BOOT_EXPORT
void ExportFlashDiagnosticRecord(const char* kind, const std::string& payload) {
    // Bound each record; a truncated or missing record is rejected by the host.
    if (payload.empty() || payload.size() > 8192) {
        ESP_LOGE(TAG, "E1.42.3 diagnostic payload size rejected");
        return;
    }
    std::printf("[DESKBOT_E1423 %s bytes=%u] %s\n", kind,
                static_cast<unsigned>(payload.size()), payload.c_str());
    std::fflush(stdout);
}

void ExportFlashDiagnosticsOnce(void*) {
    // Board construction must finish first. Never run on the UI/main loop.
    auto& app = Application::GetInstance();
    DeviceState previous_state = kDeviceStateUnknown;
    unsigned stable_observations = 0;
    unsigned quiet_observations = 0;
    for (unsigned seconds = 0; seconds < 120; ++seconds) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        const DeviceState state = app.GetDeviceState();
        stable_observations = state == previous_state ? stable_observations + 1 : 1;
        previous_state = state;
        // Both states occur after audio initialization. Offline provisioning
        // never becomes Idle; wait past AP startup and its spoken prompt.
        const bool eligible = state == kDeviceStateIdle
            || (state == kDeviceStateWifiConfiguring && stable_observations >= 10);
        if (eligible && app.GetAudioService().IsIdle()) {
            ++quiet_observations;
        } else {
            quiet_observations = 0;
        }
        if (quiet_observations == 3) {
            ESP_LOGI(TAG, "E1.42.3 export state=%d; audio queues observed idle", state);
            ExportFlashDiagnosticRecord("capacity", BuildFlashCapacityReport());
            ExportFlashDiagnosticRecord("probe", BuildFlashAddressingProbeReport());
            ESP_LOGI(TAG, "E1.42.3 read-only export complete; task exits");
            vTaskDelete(nullptr);
            return;
        }
    }
    ESP_LOGW(TAG, "E1.42.3 export skipped: quiet-state timeout; task exits");
    vTaskDelete(nullptr);
}
#endif

}  // namespace

#if CONFIG_DESKBOT_UPPER_PATTERN_BOOT_EXPORT
#include "upper_pattern_probe.h"
#endif
#if CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT
#include "continuous_mmap_probe.h"
#endif

void DevTools::Initialize(EspS3Cat* board)
{
#if CONFIG_DESKBOT_STUDIO_IDENTITY_ENABLE
    deskbot_studio_link::Start();
#endif
#if CONFIG_DESKBOT_CONTINUOUS_MMAP_BOOT_EXPORT
    deskbot_continuous_mmap::Start();
#endif
#if CONFIG_DESKBOT_UPPER_PATTERN_BOOT_EXPORT
    deskbot_upper_pattern::Start();
#endif
#if CONFIG_DESKBOT_FLASH_PROBE_BOOT_EXPORT
    if (xTaskCreate(ExportFlashDiagnosticsOnce, "flash_diag_once", 6144,
                    nullptr, 1, nullptr) != pdPASS) {
        ESP_LOGE(TAG, "E1.42.3 diagnostic task allocation failed");
    }
#endif
    auto &mcp_server = McpServer::GetInstance();
    char buffer[1024];
    const bool is_zh = (std::strncmp(Lang::CODE, "zh", 2) == 0);

    // AI-visible but bounded: unlike self.get_system_info, this omits device
    // identifiers and application hashes and does not imply upper-half mmap.
    mcp_server.AddTool("self.get_flash_capacity",
        is_zh ? "只读查询 Flash 容量和分区标签/地址/大小；不返回 MAC、UUID 或应用哈希，也不执行写入。"
              : "Read-only Flash capacity and partition labels/addresses/sizes; returns no device identifiers or app hashes and performs no writes.",
        PropertyList(), [](const PropertyList&) -> ReturnValue {
            return BuildFlashCapacityReport();
        });

    mcp_server.AddTool("self.probe_flash_addressing",
        is_zh ? "只读验证 16 MiB 边界两侧的 Flash 读取、CPU 映射、释放和内存余量；不写入 Flash。"
              : "Read-only probe of Flash reads, CPU mappings, releases, and memory around the 16 MiB boundary; never writes Flash.",
        PropertyList(), [](const PropertyList&) -> ReturnValue {
            return BuildFlashAddressingProbeReport();
        });

    // Echo base action control
    std::snprintf(buffer, sizeof(buffer), "%s",
                  is_zh ?
                  "底座动作控制。可选动作:\n"
                  "shark_head: 摇头动作\n"
                  "shark_head_decay: 缓慢摇头动作\n"
                  "look_around: 环顾四周动作\n"
                  "beat_swing: 节拍摇摆动作\n"
                  "cat_nuzzle: 蹭头撒娇动作\n"
                  "calibrate: 校准底座\n"
                  "go_home: 返回主页面\n"
                  :
                  "Echo base action control. Available actions:\n"
                  "shark_head: shake head\n"
                  "shark_head_decay: slow shake head\n"
                  "look_around: look around\n"
                  "beat_swing: beat swing\n"
                  "cat_nuzzle: cat nuzzle\n"
                  "calibrate: calibrate base\n"
                  "go_home: switch to home page\n");
    mcp_server.AddTool("self.echo_base.set_action", buffer,
    PropertyList({
        Property("action", kPropertyTypeString),
    }), [board](const PropertyList & properties) -> ReturnValue {
        const std::string &action = properties["action"].value<std::string>();
        int action_value = -1;

        ESP_LOGI(TAG, "&&& Do Action: %s", action.c_str());
        if (action == "shark_head") {
            action_value = VOCAT_BASE_CMD_SET_ACTION_SHARK_HEAD;
        } else if (action == "shark_head_decay") {
            action_value = VOCAT_BASE_CMD_SET_ACTION_SHARK_HEAD_DECAY;
        } else if (action == "look_around")
        {
            action_value = VOCAT_BASE_CMD_SET_ACTION_LOOK_AROUND;
        } else if (action == "beat_swing")
        {
            action_value = VOCAT_BASE_CMD_SET_ACTION_BEAT_SWING;
        } else if (action == "cat_nuzzle")
        {
            action_value = VOCAT_BASE_CMD_SET_ACTION_CAT_NUZZLE;
        } else if (action == "calibrate")
        {
            vocat_base_control_set_calibrate();
            BaseControl* base_control = board->GetBaseControl();
            if (base_control != nullptr) {
                bool completed = base_control->WaitForCalibrationComplete(30000);
                if (!completed) {
                    ESP_LOGW(TAG, "Calibration wait timeout");
                    return false;
                }
            }
        } else if (action == "go_home")
        {
            ui_bridge_switch_page(UI_BRIDGE_PAGE_HOME);
        } else
        {
            return false;
        }

        if (action_value != -1)
        {
            esp_err_t ret = vocat_base_control_set_action(action_value);
            if (ret != ESP_OK) {
                ESP_LOGE(TAG, "Failed to set action: %d", ret);
                return false;
            }
        }
        return true;
    });

    // Read-only base event journal query. The snapshot is copied with a
    // zero-wait lock; JSON formatting happens after the lock is released.
    mcp_server.AddTool("self.echo_base.events",
        is_zh ? "读取最近的底座事件和连接状态（只读，不触发动作）"
              : "Read recent echo base events and link state (read-only).",
        PropertyList(), [board](const PropertyList&) -> ReturnValue {
            if (board == nullptr || board->GetBaseControl() == nullptr) {
                return base_event_query::JournalUnavailable();
            }
            const auto& journal = board->GetBaseControl()->EventJournal();
            const uint64_t sampled_at_ms = static_cast<uint64_t>(
                esp_timer_get_time() / 1000);
            return base_event_query::Describe(&journal, sampled_at_ms);
        });

    // O2f2 read-only magnetic shell queries. These use the O2d RAM observer
    // only; no learning, persistence, UART command, task, or UI side effect.
    mcp_server.AddTool("self.shell.identify",
        is_zh ? "查询当前磁场外壳识别状态，包括外壳身份、磁场数据、稳定性和置信度（只读，不登记外壳）。"
              : "Query the current magnetic shell identity, field data, stability, and confidence (read-only; does not register a shell).",
        PropertyList(), [board](const PropertyList&) -> ReturnValue {
            const BaseControl* base_control =
                board == nullptr ? nullptr : board->GetBaseControl();
            const int64_t now_ms = esp_timer_get_time() / 1000;
            return base_magnetic_query::Identify(base_control, now_ms);
        });

    mcp_server.AddTool("self.shell.list",
        is_zh ? "列出当前已经登记在内存中的外壳档案（只读）；重启后清空，空列表表示当前没有登记档案。"
              : "List shell profiles currently registered in RAM (read-only); profiles are cleared on reboot.",
        PropertyList(), [board](const PropertyList&) -> ReturnValue {
            const BaseControl* base_control =
                board == nullptr ? nullptr : board->GetBaseControl();
            return base_magnetic_query::List(base_control);
        });

#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    // Explicitly enabled E1.26 tools. Both operations are volatile and never
    // command the display, player, base, network or persistent storage.
    mcp_server.AddTool("self.expression.observation",
        is_zh ? "读取最近的表情语义调用记录（只读、易失；仅表示调用到达，不代表资源已加载或屏幕已呈现）。"
              : "Read recent volatile expression semantic calls (read-only; call arrival does not prove loading or rendering).",
        PropertyList(), [](const PropertyList&) -> ReturnValue {
            const uint32_t now_ms = static_cast<uint32_t>(
                esp_timer_get_time() / 1000);
            return ExpressionRuntimeObserver::GetInstance().Describe(now_ms);
        });

    mcp_server.AddTool("self.expression.observation.clear",
        is_zh ? "清除本次启动期间的易失表情调用记录和丢弃计数（不会重置序号）。"
              : "Clear volatile expression-call records and drop counters for this boot; the sequence is preserved.",
        PropertyList(), [](const PropertyList&) -> ReturnValue {
            return ExpressionRuntimeObserver::GetInstance().Clear();
        });
#endif

    // Audio analysis mode control
    std::snprintf(buffer, sizeof(buffer), "%s",
                  is_zh ?
                  "设置音频分析模式。可选模式:\n"
                  "beat_detection: 鼓点检测模式\n"
                  "doa_follow: 声源方向跟随模式\n"
                  "disabled: 关闭音频分析"
                  :
                  "Set audio analysis mode. Available modes:\n"
                  "beat_detection: beat detection mode\n"
                  "doa_follow: DOA follow mode\n"
                  "disabled: disable audio analysis");
    mcp_server.AddTool("self.echo_base.set_audio_mode", buffer,
    PropertyList({
        Property("mode", kPropertyTypeString),
    }), [board](const PropertyList & properties) -> ReturnValue {
        const std::string &mode = properties["mode"].value<std::string>();
        AudioAnalysisMode analysis_mode = AudioAnalysisMode::DISABLED;

        if (mode == "beat_detection") {
            analysis_mode = AudioAnalysisMode::BEAT_DETECTION;
        } else if (mode == "doa_follow") {
            analysis_mode = AudioAnalysisMode::DOA_FOLLOW;
        } else if (mode == "disabled")
        {
            analysis_mode = AudioAnalysisMode::DISABLED;
        } else
        {
            ESP_LOGE(TAG, "Unknown audio analysis mode: %s", mode.c_str());
            return false;
        }

        board->SetAudioAnalysisMode(analysis_mode);
        ESP_LOGI(TAG, "Audio analysis mode set to: %s", mode.c_str());
        return true;
    });

    // Pomodoro timer control
    mcp_server.AddTool("self.pomodoro.start",
    is_zh ? "开启番茄钟定时器，设置倒计时时间（1-60分钟，默认5分钟）"
          : "Start pomodoro timer with countdown minutes (1-60, default 5).",
    PropertyList({
        Property("minutes", kPropertyTypeInteger, 5, 1, 60),
    }), [](const PropertyList& properties) -> ReturnValue {
        int minutes = properties["minutes"].value<int>();
        ESP_LOGI(TAG, "Starting pomodoro timer with %d minutes", minutes);
        alarm_start_pomodoro(minutes);
        return true;
    });

    // Pomodoro timer control (start/pause)
    mcp_server.AddTool("self.pomodoro.control",
    is_zh ? "控制番茄钟运行状态。参数：start-启动，pause-暂停"
          : "Control pomodoro running state. action: start or pause.",
    PropertyList({
        Property("action", kPropertyTypeString),
    }), [](const PropertyList& properties) -> ReturnValue {
        const std::string &action = properties["action"].value<std::string>();
        
        bool success = false;
        if (action == "start") {
            ESP_LOGI(TAG, "Starting pomodoro timer");
            success = alarm_resume_pomodoro();
        } else if (action == "pause") {
            ESP_LOGI(TAG, "Pausing pomodoro timer");
            success = alarm_pause_pomodoro();
        } else {
            ESP_LOGE(TAG, "Unknown pomodoro action: %s (expected 'start' or 'pause')", action.c_str());
            return false;
        }
        return success;
    });

    // Sleep timer control
    mcp_server.AddTool("self.sleep.start",
    is_zh ? "设置睡眠闹钟，从当前时间到指定结束时间（24小时制）。参数：end_hour(0-23), end_min(0-59)"
          : "Set sleep timer from current time to specified end time (24h). params: end_hour(0-23), end_min(0-59).",
    PropertyList({
        Property("end_hour", kPropertyTypeInteger, 8, 0, 23),
        Property("end_min", kPropertyTypeInteger, 0, 0, 59),
    }), [](const PropertyList& properties) -> ReturnValue {
        int end_hour = properties["end_hour"].value<int>();
        int end_min = properties["end_min"].value<int>();
        
        // Validate parameters
        if (end_hour < 0 || end_hour >= 24) {
            ESP_LOGE(TAG, "Invalid end_hour: %d (must be 0-23)", end_hour);
            return false;
        }
        if (end_min < 0 || end_min >= 60) {
            ESP_LOGE(TAG, "Invalid end_min: %d (must be 0-59)", end_min);
            return false;
        }
        
        ESP_LOGI(TAG, "Setting sleep timer: current time -> %02d:%02d", end_hour, end_min);
        alarm_start_sleep(end_hour, end_min);
        return true;
    });
}
