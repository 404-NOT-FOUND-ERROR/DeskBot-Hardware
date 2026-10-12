#ifndef MAGNETIC_SHELL_MANAGER_H
#define MAGNETIC_SHELL_MANAGER_H

#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

class MagneticShellManager {
public:
    static constexpr size_t kMaxProfiles = 6;
    static constexpr size_t kLearningSamples = 24;

    enum class RecognitionState {
        NoData,
        Unstable,
        NoProfiles,
        Matched,
        Unknown,
    };

    enum class EventType {
        None,
        Attached,
        Changed,
        Unknown,
        Detached,
    };

    struct Profile {
        int id = 0;
        std::string name;
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int magnitude = 0;
        int noise = 0;
        int tolerance = 110;
        uint32_t sample_count = 0;
    };

    struct RecognitionEvent {
        uint32_t sequence = 0;
        EventType type = EventType::None;
        int profile_id = 0;
        std::string profile_name;
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int magnitude = 0;
        int confidence = 0;
        int distance = 0;
        int64_t timestamp_ms = 0;
    };

    struct Snapshot {
        bool has_data = false;
        bool stable = false;
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int magnitude = 0;
        int noise = 0;
        int64_t last_sample_ms = 0;
        RecognitionState recognition = RecognitionState::NoData;
        int matched_profile_id = 0;
        std::string matched_name;
        int confidence = 0;
        int distance = 0;
        bool learning = false;
        size_t learning_samples = 0;
        std::string learning_name;
        std::string last_result;
        RecognitionEvent last_event;
        size_t profile_count = 0;
        std::vector<RecognitionEvent> recent_events;
        std::vector<Profile> profiles;
    };

    static MagneticShellManager& GetInstance();

    void Initialize();
    void AddSample(int16_t x, int16_t y, int16_t z, int64_t timestamp_ms);
    void NotifySensorUnavailable(int64_t timestamp_ms);
    bool StartLearning(const std::string& requested_name, std::string* message = nullptr);
    bool RenameProfile(int id, const std::string& name, std::string* message = nullptr);
    bool DeleteProfile(int id, std::string* message = nullptr);
    Snapshot GetSnapshot(bool include_history = true) const;
    std::string DescribeCurrent() const;
    std::string DescribeProfiles() const;
    std::string DescribeEvents() const;

private:
    struct Sample {
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int64_t timestamp_ms = 0;
    };

    MagneticShellManager();
    ~MagneticShellManager();
    MagneticShellManager(const MagneticShellManager&) = delete;
    MagneticShellManager& operator=(const MagneticShellManager&) = delete;

    void LoadProfilesLocked();
    bool SaveProfilesLocked();
    void UpdateRecognitionLocked();
    void UpdateEventStateLocked(int64_t timestamp_ms);
    void EmitEventLocked(EventType type, int64_t timestamp_ms);
    void FinishLearningLocked();
    std::string NormalizeNameLocked(const std::string& requested_name) const;
    int NextProfileIdLocked() const;

    mutable SemaphoreHandle_t mutex_ = nullptr;
    bool initialized_ = false;
    std::array<Sample, 20> window_{};
    size_t window_count_ = 0;
    size_t window_next_ = 0;
    std::vector<Profile> profiles_;

    bool has_data_ = false;
    bool stable_ = false;
    int16_t filtered_x_ = 0;
    int16_t filtered_y_ = 0;
    int16_t filtered_z_ = 0;
    int filtered_magnitude_ = 0;
    int noise_ = 0;
    int64_t last_sample_ms_ = 0;
    RecognitionState recognition_ = RecognitionState::NoData;
    int matched_profile_id_ = 0;
    std::string matched_name_;
    int confidence_ = 0;
    int distance_ = 0;

    bool learning_ = false;
    std::string learning_name_;
    size_t learning_count_ = 0;
    int64_t learning_sum_x_ = 0;
    int64_t learning_sum_y_ = 0;
    int64_t learning_sum_z_ = 0;
    int64_t learning_sum_x2_ = 0;
    int64_t learning_sum_y2_ = 0;
    int64_t learning_sum_z2_ = 0;
    std::string last_result_;

    static constexpr size_t kRecentEventCapacity = 8;
    std::array<RecognitionEvent, kRecentEventCapacity> recent_events_{};
    size_t recent_event_count_ = 0;
    size_t recent_event_next_ = 0;
    uint32_t next_event_sequence_ = 1;
    RecognitionState committed_event_state_ = RecognitionState::NoData;
    int committed_event_profile_id_ = 0;
    RecognitionState candidate_event_state_ = RecognitionState::NoData;
    int candidate_event_profile_id_ = 0;
    size_t candidate_event_count_ = 0;
    int64_t last_event_ms_ = 0;
};

#endif // MAGNETIC_SHELL_MANAGER_H
