#ifndef BASE_CONTROL_H
#define BASE_CONTROL_H

#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <cstdint>

#include "base_event_journal.h"
#include "base_magnetic_observer.h"

class EspS3Cat;
class Display;

class BaseControl {
public:
    BaseControl(EspS3Cat* board);
    ~BaseControl();

    void Initialize();
    bool IsOnline() const
    {
        return vocat_base_online_;
    }
    void HandleCommand(uint8_t cmd, uint8_t *data, int data_len);

    // Wait for calibration to complete (until VOCAT_BASE_CMD_RECV_CALIBRATE_STEP2)
    // Returns true if calibration completed, false if timeout
    bool WaitForCalibrationComplete(int timeout_ms = 30000);

    const BaseEventJournal& EventJournal() const { return event_journal_; }

    // Read-only bridge for the later MCP/client layer.  This candidate does
    // not register a tool or render a page; the method exists to keep the
    // recognition core independently testable and bounded.
    BaseMagneticRecognition::Snapshot GetMagneticRecognitionSnapshot(
        int64_t now_ms) const
    {
        BaseMagneticRecognition::Snapshot snapshot{};
        (void)magnetic_observer_.TryRead(now_ms, &snapshot);
        return snapshot;
    }

    // O2f2 keeps the O2d observer and recognition core unchanged.  These
    // bounded delegates expose only the already-existing RAM snapshot/profile
    // copies to a read-only query layer; they do not add learning or storage.
    BaseMagneticObserver::QueryResult IdentifyMagnetic(
        int64_t now_ms, BaseMagneticRecognition::Snapshot* snapshot) const
    {
        return magnetic_observer_.Identify(now_ms, snapshot);
    }

    BaseMagneticObserver::QueryResult ListMagneticProfiles(
        BaseMagneticRecognition::Profile* output, std::size_t capacity,
        std::size_t* count) const
    {
        return magnetic_observer_.List(output, capacity, count);
    }

private:
    EspS3Cat* board_;
    bool vocat_base_online_;
    int64_t last_heartbeat_time_;
    esp_timer_handle_t heartbeat_check_timer_;
    SemaphoreHandle_t calibrate_semaphore_;
    BaseEventJournal event_journal_;
    BaseMagneticObserver magnetic_observer_;
    
    static constexpr int64_t HEARTBEAT_TIMEOUT_MS = 1000;  // 1 second timeout

    static void HeartbeatCheckTimerCallback(void* arg);
    static void CmdCallback(uint8_t cmd, uint8_t *data, int data_len, void *user_ctx);
};

#endif // BASE_CONTROL_H
