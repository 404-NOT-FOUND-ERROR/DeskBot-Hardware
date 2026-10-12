#ifndef BASE_CONTROL_H
#define BASE_CONTROL_H

#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <cstdint>
#include <vector>

#include "base_event_journal.h"
#include "base_event_policy.h"
#include "base_event_state.h"

class EspS3Cat;
class Display;

class BaseControl {
public:
    enum class MagneticSensorKind : uint8_t {
        Unknown = 0,
        Bmm150 = 1,
        Qmc6309 = 2,
        Ss49e = 3,
    };

    struct MagneticTelemetry {
        bool valid = false;
        MagneticSensorKind sensor_kind = MagneticSensorKind::Unknown;
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        uint32_t sample_count = 0;
        int64_t last_sample_ms = 0;
    };

    struct MagneticSample {
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int64_t timestamp_ms = 0;
    };

    BaseControl(EspS3Cat* board);
    ~BaseControl();

    void Initialize();
    bool IsOnline() const;
    void HandleCommand(uint8_t cmd, uint8_t *data, int data_len);
    bool GetMagneticTelemetry(MagneticTelemetry* telemetry) const;
    uint16_t GetMechanicalStatus(bool* valid) const;
    static BaseControl* GetInstance();

    // Returns a copy of the recent validated non-heartbeat UART events.
    std::vector<BaseEventJournal::Event> GetRecentEvents() const
    {
        return event_journal_.Snapshot();
    }

    // Wait for calibration to complete (until VOCAT_BASE_CMD_RECV_CALIBRATE_STEP2)
    // Returns true if calibration completed, false if timeout
    bool WaitForCalibrationComplete(int timeout_ms = 30000);

private:
    EspS3Cat* board_;
    esp_timer_handle_t heartbeat_check_timer_;
    SemaphoreHandle_t calibrate_semaphore_;
    SemaphoreHandle_t magnetic_mutex_;
    SemaphoreHandle_t state_mutex_;
    MagneticTelemetry magnetic_telemetry_;
    QueueHandle_t magnetic_sample_queue_;
    TaskHandle_t magnetic_sample_task_;
    BaseEventJournal event_journal_;
    BaseEventPolicy event_policy_;
    BaseEventStateTracker event_state_;
    
    static constexpr int64_t HEARTBEAT_TIMEOUT_MS = 1000;  // 1 second timeout

    static void HeartbeatCheckTimerCallback(void* arg);
    static void MagneticSampleTask(void* arg);
    static void CmdCallback(uint8_t cmd, uint8_t *data, int data_len, void *user_ctx);

    bool LockState(TickType_t timeout_ticks = pdMS_TO_TICKS(5)) const;
    void UnlockState() const;
};

#endif // BASE_CONTROL_H
