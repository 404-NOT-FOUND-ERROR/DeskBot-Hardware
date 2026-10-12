#include "base_control.h"
#include "esp_vocat.h"
#include "vocat_base_control.h"
#include "display/display.h"
#include "display/emote_display.h"
#include "config.h"
#include "application.h"
#include "assets/lang_config.h"
#include "device_state.h"
#include <esp_log.h>
#include <esp_timer.h>
#include <cstdio>
#include <cstring>
#include "customer_ui/alarm_manager.h"
#include "customer_ui/alarm_api.h"
#include "magnetic_shell_manager.h"
#include "base_event_mapper.h"

#define TAG "BaseControl"

// The public base-control component does not expose the periodic mechanical
// status command as a named receive constant.  Keep it local so the official
// dispatch path can acknowledge it without treating every 500 ms status frame
// as an unknown command.
static constexpr uint8_t kRecvMechanicalStatus = 0x05;

static BaseControl* s_base_control_instance = nullptr;

static void lvgl_muyu_click_cb(void *arg)
{
    (void)arg;
    lvgl_muyu_click();
}

// Base UART callbacks run on uart_rx_1.  Keep all page and emote mutations on
// the application's serialized event loop so a motor/status frame cannot race
// the circular-screen gesture handler or the LVGL flush path.
static void handle_base_ui_event(EspS3Cat* board, uint16_t event)
{
    if (board == nullptr) {
        return;
    }

    Display* display = board->GetDisplay();
    if (display == nullptr) {
        return;
    }

    auto& app = Application::GetInstance();
    auto* emote_display = dynamic_cast<emote::EmoteDisplay*>(display);

    switch (event) {
    case VOCAT_BASE_CMD_RECV_SWITCH_SLIDE_DOWN:
        ESP_LOGI(TAG, "Slide switch down");
        app.ToggleChatState();
        break;
    case VOCAT_BASE_CMD_RECV_SWITCH_SLIDE_UP: {
        ESP_LOGI(TAG, "Slide switch up");
        const char* page = ui_bridge_get_current_page();
        if (page != nullptr && strcmp(page, PAGE_POMODORO) == 0) {
            alarm_start_pomodoro(5);
            ESP_LOGI(TAG, "Restart POMODORO");
        } else {
            app.ToggleChatState();
        }
        break;
    }
    case VOCAT_BASE_CMD_RECV_SWITCH_SINGLE_CLICK: {
        ESP_LOGI(TAG, "Single click");
        const char* page = ui_bridge_get_current_page();
        if (page != nullptr && strcmp(page, PAGE_MUYU) == 0) {
            // Muyu itself is an LVGL page, so retain the final hop through
            // LVGL's async queue even though this callback is serialized.
            (void)lv_async_call(lvgl_muyu_click_cb, nullptr);
            ESP_LOGI(TAG, "Set MAIN_EVENT_PLAY_MUYU");
        }
        break;
    }
    case VOCAT_BASE_CMD_RECV_CALIBRATE_START:
        ESP_LOGI(TAG, "Calibrate start");
        display->SetChatMessage("system", Lang::Strings::CALIBRATING_STEP1);
        break;
    case VOCAT_BASE_CMD_RECV_CALIBRATE_STEP1:
        ESP_LOGI(TAG, "Calibrate step 1 Done");
        display->SetChatMessage("system", Lang::Strings::CALIBRATING_STEP2);
        break;
    case VOCAT_BASE_CMD_RECV_CALIBRATE_STEP2:
        ESP_LOGI(TAG, "Calibrate step 2 Done");
        display->SetChatMessage("system", Lang::Strings::CALIBRATING_STEP3);
        break;
    case VOCAT_BASE_CMD_RECV_SWITCH_FISH_ATTACHED:
        ESP_LOGI(TAG, "Fish attached");
        if (emote_display != nullptr) {
            (void)emote_display->InsertAnimDialog("eat", 3500);
        }
        break;
    case VOCAT_BASE_CMD_RECV_SWITCH_PAIR_DETECT:
        ESP_LOGI(TAG, "Pair detect");
        if (emote_display != nullptr) {
            emote_display->SetEmotion("happy");
        }
        // Keep the official pairing side effect after the UI update.
        (void)vocat_base_control_set_action(VOCAT_BASE_CMD_SET_ACTION_LOOK_AROUND);
        break;
    default:
        ESP_LOGI(TAG, "Unhandled scheduled base UI event: %u", event);
        break;
    }
}

static void schedule_base_ui_event(EspS3Cat* board, uint16_t event)
{
    Application::GetInstance().Schedule([board, event]() {
        handle_base_ui_event(board, event);
    });
}

static void schedule_base_connected_animation(EspS3Cat* board)
{
    Application::GetInstance().Schedule([board]() {
        if (board == nullptr) {
            return;
        }
        auto* display = board->GetDisplay();
        auto* emote_display = display != nullptr
            ? dynamic_cast<emote::EmoteDisplay*>(display)
            : nullptr;
        if (emote_display != nullptr) {
            (void)emote_display->InsertAnimDialog("insert", 3000);
        }
    });
}

BaseControl::BaseControl(EspS3Cat* board) : board_(board)
{
    heartbeat_check_timer_ = nullptr;
    calibrate_semaphore_ = xSemaphoreCreateBinary();
    magnetic_mutex_ = xSemaphoreCreateMutex();
    state_mutex_ = xSemaphoreCreateMutex();
    magnetic_sample_queue_ = nullptr;
    magnetic_sample_task_ = nullptr;
    s_base_control_instance = this;

    if (calibrate_semaphore_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create calibrate semaphore");
    }
    if (magnetic_mutex_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create magnetic telemetry mutex");
    }
    if (state_mutex_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create base state mutex");
    }
}

BaseControl::~BaseControl()
{
    if (magnetic_sample_task_ != nullptr) {
        vTaskDelete(magnetic_sample_task_);
        magnetic_sample_task_ = nullptr;
    }
    if (magnetic_sample_queue_ != nullptr) {
        vQueueDelete(magnetic_sample_queue_);
        magnetic_sample_queue_ = nullptr;
    }
    if (heartbeat_check_timer_ != nullptr) {
        esp_timer_stop(heartbeat_check_timer_);
        esp_timer_delete(heartbeat_check_timer_);
    }
    if (calibrate_semaphore_ != nullptr) {
        vSemaphoreDelete(calibrate_semaphore_);
    }
    if (magnetic_mutex_ != nullptr) {
        vSemaphoreDelete(magnetic_mutex_);
    }
    if (state_mutex_ != nullptr) {
        vSemaphoreDelete(state_mutex_);
    }
    if (s_base_control_instance == this) {
        s_base_control_instance = nullptr;
    }
}

BaseControl* BaseControl::GetInstance()
{
    return s_base_control_instance;
}

bool BaseControl::LockState(TickType_t timeout_ticks) const
{
    return state_mutex_ != nullptr &&
           xSemaphoreTake(state_mutex_, timeout_ticks) == pdTRUE;
}

void BaseControl::UnlockState() const
{
    if (state_mutex_ != nullptr) {
        xSemaphoreGive(state_mutex_);
    }
}

bool BaseControl::IsOnline() const
{
    if (!LockState()) {
        return false;
    }
    const bool online = event_state_.online();
    UnlockState();
    return online;
}

bool BaseControl::GetMagneticTelemetry(MagneticTelemetry* telemetry) const
{
    if (telemetry == nullptr || magnetic_mutex_ == nullptr) {
        return false;
    }
    if (xSemaphoreTake(magnetic_mutex_, pdMS_TO_TICKS(5)) != pdTRUE) {
        return false;
    }
    *telemetry = magnetic_telemetry_;
    xSemaphoreGive(magnetic_mutex_);
    return telemetry->valid;
}

uint16_t BaseControl::GetMechanicalStatus(bool* valid) const
{
    if (!LockState()) {
        if (valid != nullptr) {
            *valid = false;
        }
        return 0;
    }
    const bool status_valid = event_state_.mechanical_status_valid();
    const uint16_t status = event_state_.mechanical_status();
    UnlockState();
    if (valid != nullptr) {
        *valid = status_valid;
    }
    return status;
}

void BaseControl::Initialize()
{
    MagneticShellManager::GetInstance().Initialize();

    magnetic_sample_queue_ = xQueueCreate(8, sizeof(MagneticSample));
    if (magnetic_sample_queue_ == nullptr) {
        ESP_LOGE(TAG, "Failed to create magnetic sample queue");
    } else if (xTaskCreate(&MagneticSampleTask, "mag_shell", 4096, this, 1,
                          &magnetic_sample_task_) != pdPASS) {
        ESP_LOGE(TAG, "Failed to create magnetic sample task");
        vQueueDelete(magnetic_sample_queue_);
        magnetic_sample_queue_ = nullptr;
    }

    vocat_base_control_config_t base_config = {
        .uart_num = UART_NUM_1,
        .tx_pin = UART1_TX,
        .rx_pin = UART1_RX,
        .baud_rate = 115200,
        .rx_buffer_size = 1024,
        .cmd_cb = CmdCallback,
        .user_ctx = this,
    };
    esp_err_t ret = vocat_base_control_init(&base_config);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize echo base control");
        return;
    }

    // Initialize heartbeat monitoring timer
    const esp_timer_create_args_t heartbeat_timer_args = {
        .callback = &HeartbeatCheckTimerCallback,
        .arg = this,
        .name = "heartbeat_check"
    };
    ret = esp_timer_create(&heartbeat_timer_args, &heartbeat_check_timer_);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create heartbeat check timer");
        return;
    }

    // Start periodic timer to check heartbeat every 500ms
    ret = esp_timer_start_periodic(heartbeat_check_timer_, 500 * 1000);  // 500ms in microseconds
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start heartbeat check timer");
        esp_timer_delete(heartbeat_check_timer_);
        heartbeat_check_timer_ = nullptr;
        return;
    }

    // Initialize to offline state
    if (LockState()) {
        event_state_.Reset();
        UnlockState();
    }
}

void BaseControl::HandleCommand(uint8_t cmd, uint8_t *data, int data_len)
{
    if (cmd == VOCAT_BASE_CMD_RECV_PERCEPTION && data_len == 7 && data != nullptr &&
        magnetic_mutex_ != nullptr) {
        const auto decode_i16 = [data](int index) {
            const uint16_t bits = (static_cast<uint16_t>(data[index]) << 8) |
                                  static_cast<uint16_t>(data[index + 1]);
            return static_cast<int16_t>(bits);
        };
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int64_t timestamp_ms = esp_timer_get_time() / 1000;
        if (xSemaphoreTake(magnetic_mutex_, pdMS_TO_TICKS(5)) == pdTRUE) {
            const uint8_t sensor_kind = data[0];
            magnetic_telemetry_.sensor_kind = sensor_kind <= 3
                ? static_cast<MagneticSensorKind>(sensor_kind)
                : MagneticSensorKind::Unknown;
            x = magnetic_telemetry_.x = decode_i16(1);
            y = magnetic_telemetry_.y = decode_i16(3);
            z = magnetic_telemetry_.z = decode_i16(5);
            magnetic_telemetry_.valid = true;
            magnetic_telemetry_.last_sample_ms = timestamp_ms;
            ++magnetic_telemetry_.sample_count;
            xSemaphoreGive(magnetic_mutex_);
            // Keep UART receive latency bounded. Recognition and persistence
            // run on a separate low-priority task instead of uart_rx_1.
            if (magnetic_sample_queue_ != nullptr) {
                const MagneticSample sample{x, y, z, timestamp_ms};
                if (xQueueSend(magnetic_sample_queue_, &sample, 0) != pdTRUE) {
                    MagneticSample dropped;
                    (void)xQueueReceive(magnetic_sample_queue_, &dropped, 0);
                    (void)xQueueSend(magnetic_sample_queue_, &sample, 0);
                }
            }
        }
    }

    const int64_t event_time_ms = esp_timer_get_time() / 1000;
    const auto mapped = base_event_mapper::Map(cmd, data, data_len);
    bool base_just_connected = false;
    if (cmd == VOCAT_BASE_CMD_RECV_HEARTBEAT && !mapped.ignored) {
        if (mapped.known) {
            if (LockState()) {
                base_just_connected = event_state_.HandleHeartbeat(event_time_ms);
                UnlockState();
            }
            if (base_just_connected) {
                event_journal_.Record(mapped.type, cmd, data, data_len, event_time_ms,
                                      mapped.value, mapped.has_value);
                ESP_LOGI(TAG, "EventJournal: seq=%lu type=%s cmd=0x%02X value=0x%04X",
                         static_cast<unsigned long>(event_journal_.TotalRecorded()),
                         mapped.type, cmd, mapped.value);
            }
        } else {
            event_journal_.Record(mapped.type, cmd, data, data_len, event_time_ms,
                                  mapped.value, mapped.has_value);
            ESP_LOGW(TAG, "Unknown base event recorded: cmd=0x%02X value=0x%04X",
                     cmd, mapped.value);
        }
    }

    // Heartbeats are recorded only on an offline -> online transition.
    // Perception samples remain in the existing telemetry/shell pipeline.
    if (!mapped.ignored && cmd != VOCAT_BASE_CMD_RECV_HEARTBEAT) {
        bool should_record = true;
        if (mapped.mechanical_status) {
            if (LockState()) {
                should_record = event_state_.UpdateMechanicalStatus(mapped.value);
                UnlockState();
            }
        }
        if (should_record) {
            event_journal_.Record(mapped.type, cmd, data, data_len, event_time_ms,
                                  mapped.value, mapped.has_value);
            ESP_LOGI(TAG, "EventJournal: seq=%lu type=%s cmd=0x%02X value=0x%04X",
                     static_cast<unsigned long>(event_journal_.TotalRecorded()),
                     mapped.type, cmd, mapped.value);
        }
    }

    // Action completion remains in the journal and MCP query.  Do not enqueue
    // an Emote Toast from the UART callback: the device's Emote renderer and
    // LVGL flush path share the display pipeline, and this additive UI effect
    // can leave LVGL waiting for a flush after a motor animation.

    if (cmd != VOCAT_BASE_CMD_RECV_HEARTBEAT && cmd != VOCAT_BASE_CMD_RECV_PERCEPTION) {
        // Mechanical status is reported by the base every 500 ms. Keep this
        // diagnostic at debug level so a connected console cannot become a
        // second real-time workload during normal operation.
        ESP_LOGD(TAG, "Handle: cmd=%02X, data_len=%d", cmd, data_len);
    }

    switch (cmd) {
    case kRecvMechanicalStatus:
        // The mapper/state tracker above already records state transitions;
        // the official command path has no further side effect for this frame.
        ESP_LOGD(TAG, "Mechanical status: 0x%04X", mapped.value);
        break;
    case VOCAT_BASE_CMD_RECV_SLIDE_SWITCH: {
        if (data_len >= 2) {
            uint16_t event = (data[0] << 8) | data[1];
            switch (event) {
            case VOCAT_BASE_CMD_RECV_SWITCH_SLIDE_DOWN:
            case VOCAT_BASE_CMD_RECV_SWITCH_SLIDE_UP:
            case VOCAT_BASE_CMD_RECV_SWITCH_SINGLE_CLICK:
            case VOCAT_BASE_CMD_RECV_CALIBRATE_START:
            case VOCAT_BASE_CMD_RECV_CALIBRATE_STEP1:
            case VOCAT_BASE_CMD_RECV_CALIBRATE_STEP2:
            case VOCAT_BASE_CMD_RECV_SWITCH_FISH_ATTACHED:
            case VOCAT_BASE_CMD_RECV_SWITCH_PAIR_DETECT:
                if (event == VOCAT_BASE_CMD_RECV_CALIBRATE_STEP2 &&
                    calibrate_semaphore_ != nullptr) {
                    // The calibration waiter is transport-facing state, not
                    // UI state, so it can be released from the UART callback.
                    xSemaphoreGive(calibrate_semaphore_);
                }
                schedule_base_ui_event(board_, event);
                break;
            default:
                ESP_LOGI(TAG, "Slide switch event: %d", event);
                break;
            }
        }
        break;
    }
    case VOCAT_BASE_CMD_RECV_PERCEPTION: {
        if (data_len != 7) {
            ESP_LOGW(TAG, "Unsupported perception payload, data_len=%d", data_len);
        }
        break;
    }
    case VOCAT_BASE_CMD_RECV_ACTION: {
        if (data == nullptr || data_len < 2) {
            ESP_LOGW(TAG, "Action response with invalid payload length: %d", data_len);
            break;
        }
        ESP_LOGI(TAG, "Action response, data_len=%d", data_len);
        uint16_t event = (data[0] << 8) | data[1];
        switch (event) {
        case VOCAT_BASE_CMD_RECV_ACTION_DONE:
            ESP_LOGI(TAG, "Action done");
            break;
        default:
            ESP_LOGI(TAG, "Unknown action event: %d", event);
            break;
        }
        break;
    }
    case VOCAT_BASE_CMD_RECV_HEARTBEAT: {
        if (!mapped.known) {
            ESP_LOGW(TAG, "Heartbeat with invalid payload length: %d", data_len);
            break;
        }
        if (base_just_connected) {
            ESP_LOGI(TAG, "Echo base connected (reinserted)");
            schedule_base_connected_animation(board_);
        }
        break;
    }
    default: {
        ESP_LOGI(TAG, "Unknown command: 0x%02X", cmd);
        break;
    }
    }
}

void BaseControl::HeartbeatCheckTimerCallback(void* arg)
{
    BaseControl* self = static_cast<BaseControl*>(arg);
    if (self == nullptr) {
        return;
    }

    if (!self->LockState()) {
        return;
    }

    int64_t current_time = esp_timer_get_time() / 1000;  // Convert to milliseconds
    const int64_t last_heartbeat_time = self->event_state_.last_heartbeat_ms();
    const int64_t time_since_last_heartbeat = current_time - last_heartbeat_time;
    const bool should_disconnect = self->event_state_.CheckHeartbeatTimeout(
        current_time, BaseControl::HEARTBEAT_TIMEOUT_MS);
    self->UnlockState();

    // Check if heartbeat timeout (2 seconds = 4 missed heartbeats at 500ms interval)
    if (should_disconnect) {
        if (self->magnetic_mutex_ != nullptr &&
            xSemaphoreTake(self->magnetic_mutex_, pdMS_TO_TICKS(5)) == pdTRUE) {
            self->magnetic_telemetry_.valid = false;
            xSemaphoreGive(self->magnetic_mutex_);
        }
        MagneticShellManager::GetInstance().NotifySensorUnavailable(current_time);
        self->event_journal_.RecordSynthetic("base.link.disconnected", 0x00,
                                             current_time);
        ESP_LOGI(TAG, "EventJournal: seq=%lu type=base.link.disconnected",
                 static_cast<unsigned long>(self->event_journal_.TotalRecorded()));
        const unsigned long timeout_ms = time_since_last_heartbeat > UINT32_MAX
                                             ? static_cast<unsigned long>(UINT32_MAX)
                                             : static_cast<unsigned long>(time_since_last_heartbeat);
        ESP_LOGW(TAG, "Echo base disconnected (timeout: %lu ms)", timeout_ms);
    }
}

void BaseControl::MagneticSampleTask(void* arg)
{
    auto* self = static_cast<BaseControl*>(arg);
    if (self == nullptr || self->magnetic_sample_queue_ == nullptr) {
        vTaskDelete(nullptr);
        return;
    }

    MagneticSample sample;
    while (xQueueReceive(self->magnetic_sample_queue_, &sample, portMAX_DELAY) == pdTRUE) {
        MagneticShellManager::GetInstance().AddSample(
            sample.x, sample.y, sample.z, sample.timestamp_ms);
    }
    vTaskDelete(nullptr);
}

bool BaseControl::WaitForCalibrationComplete(int timeout_ms)
{
    if (calibrate_semaphore_ == nullptr) {
        ESP_LOGE(TAG, "Calibrate semaphore not initialized");
        return false;
    }

    // Clear semaphore first in case it was already set
    xSemaphoreTake(calibrate_semaphore_, 0);

    // Wait for calibration to complete
    TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
    BaseType_t result = xSemaphoreTake(calibrate_semaphore_, timeout_ticks);

    if (result == pdTRUE) {
        ESP_LOGI(TAG, "Calibration completed successfully");
        return true;
    } else {
        ESP_LOGW(TAG, "Calibration wait timeout after %d ms", timeout_ms);
        return false;
    }
}

void BaseControl::CmdCallback(uint8_t cmd, uint8_t *data, int data_len, void *user_ctx)
{
    BaseControl* self = static_cast<BaseControl*>(user_ctx);
    if (self != nullptr) {
        self->HandleCommand(cmd, data, data_len);
    }
}
