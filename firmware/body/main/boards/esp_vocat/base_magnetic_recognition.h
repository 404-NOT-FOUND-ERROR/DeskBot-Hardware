#ifndef BASE_MAGNETIC_RECOGNITION_H
#define BASE_MAGNETIC_RECOGNITION_H

// Bounded, read-only magnetic recognition core.
//
// This file deliberately has no FreeRTOS, NVS, LVGL, UART, STL containers, or
// heap allocation.  The owner of the existing BaseControl snapshot mutex must
// serialize calls made from the device integration layer.  Profiles live only
// in RAM in this phase; persistence and client binding are separate layers.

#include "base_magnetic_telemetry.h"

#include <algorithm>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>

class BaseMagneticRecognition {
public:
    static constexpr std::size_t kWindowCapacity = 20;
    static constexpr std::size_t kMaxProfiles = 6;
    static constexpr std::size_t kMaxNameBytes = 36;
    static constexpr std::size_t kMinimumStableSamples = 10;
    static constexpr int64_t kMinimumStableSpanMs = 600;
    static constexpr int64_t kSampleGapResetMs = 500;
    static constexpr int64_t kStaleAfterMs = 1500;
    static constexpr int kStableNoiseLimit = 40;
    static constexpr int kMinimumTolerance = 110;
    static constexpr int kMaximumTolerance = 190;
    static constexpr int kMinimumKnownConfidence = 60;

    enum class Status : uint8_t {
        NoData = 0,
        Collecting,
        Unstable,
        Stale,
        NoProfiles,
        Known,
        Unknown,
        Ambiguous,
        UnsupportedSensor,
    };

    enum class RegistrationResult : uint8_t {
        Registered = 0,
        Replaced,
        InvalidId,
        InvalidName,
        NoData,
        NotStable,
        Stale,
        UnsupportedSensor,
        Full,
    };

    struct Profile {
        bool valid = false;
        uint8_t id = 0;
        BaseMagneticSensorKind sensor_kind = BaseMagneticSensorKind::Unknown;
        char name[kMaxNameBytes + 1]{};
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int32_t magnitude = 0;
        int noise = 0;
        int tolerance = kMinimumTolerance;
        uint32_t sample_count = 0;
    };

    struct Snapshot {
        bool has_data = false;
        bool stable = false;
        bool sensor_supported = false;
        BaseMagneticSensorKind sensor_kind = BaseMagneticSensorKind::Unknown;
        Status status = Status::NoData;

        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int32_t magnitude = 0;
        int noise = 0;
        std::size_t window_count = 0;
        int64_t first_sample_ms = 0;
        int64_t last_sample_ms = 0;
        int64_t span_ms = 0;
        // -1 means that the query timestamp precedes the last sample.
        int64_t age_ms = -1;

        // best_profile_id is diagnostic only.  matched_profile_id is non-zero
        // only for an unambiguous Known result.
        uint8_t best_profile_id = 0;
        uint8_t matched_profile_id = 0;
        // Heuristic similarity score, not a calibrated probability.
        int confidence = 0;
        int distance = -1;
        int second_distance = -1;
        std::size_t profile_count = 0;
    };

    BaseMagneticRecognition() = default;

    // Returns false for invalid/unsupported input.  A sensor change, a time
    // reversal, or a gap longer than kSampleGapResetMs starts a new window.
    bool AddSample(const BaseMagneticSample& sample, int64_t timestamp_ms)
    {
        if (!sample.valid || timestamp_ms < 0) {
            ResetWindow();
            return false;
        }

        const bool sensor_changed = has_data_ && sensor_kind_ != sample.sensor_kind;
        const bool time_reversed = has_data_ && timestamp_ms < last_sample_ms_;
        const bool gap_too_large = has_data_ &&
                                   timestamp_ms - last_sample_ms_ > kSampleGapResetMs;
        if (sensor_changed || time_reversed || gap_too_large) {
            ResetWindow();
        }

        Sample& slot = window_[window_next_];
        slot.x = sample.x;
        slot.y = sample.y;
        slot.z = sample.z;
        slot.timestamp_ms = timestamp_ms;
        window_next_ = (window_next_ + 1) % kWindowCapacity;
        if (window_count_ < kWindowCapacity) {
            ++window_count_;
        }
        has_data_ = true;
        sensor_kind_ = sample.sensor_kind;
        last_sample_ms_ = timestamp_ms;
        if (window_count_ == 1) {
            first_sample_ms_ = timestamp_ms;
        }
        return IsSupportedSensor(sensor_kind_);
    }

    void ResetSamples()
    {
        ResetWindow();
    }

    // Registers the current stable window.  An existing id is replaced.  The
    // registry is intentionally RAM-only; callers may later serialize it.
    RegistrationResult RegisterCurrent(uint8_t id, const char* name,
                                       int64_t now_ms)
    {
        if (id == 0 || id > kMaxProfiles) {
            return RegistrationResult::InvalidId;
        }
        const std::size_t name_length = NameLength(name);
        if (name_length == 0 || name_length > kMaxNameBytes ||
            !IsValidName(name, name_length)) {
            return RegistrationResult::InvalidName;
        }

        const Snapshot snapshot = GetSnapshot(now_ms);
        if (!snapshot.has_data) {
            return RegistrationResult::NoData;
        }
        if (!snapshot.sensor_supported) {
            return RegistrationResult::UnsupportedSensor;
        }
        if (snapshot.age_ms < 0 || snapshot.age_ms > kStaleAfterMs) {
            return RegistrationResult::Stale;
        }
        if (!snapshot.stable) {
            return RegistrationResult::NotStable;
        }

        std::size_t slot_index = kMaxProfiles;
        bool replacing = false;
        for (std::size_t index = 0; index < kMaxProfiles; ++index) {
            if (profiles_[index].valid && profiles_[index].id == id) {
                slot_index = index;
                replacing = true;
                break;
            }
        }
        if (slot_index == kMaxProfiles) {
            for (std::size_t index = 0; index < kMaxProfiles; ++index) {
                if (!profiles_[index].valid) {
                    slot_index = index;
                    break;
                }
            }
        }
        if (slot_index == kMaxProfiles) {
            return RegistrationResult::Full;
        }

        Profile& profile = profiles_[slot_index];
        profile = Profile{};
        profile.valid = true;
        profile.id = id;
        profile.sensor_kind = snapshot.sensor_kind;
        std::memcpy(profile.name, name, name_length);
        profile.name[name_length] = '\0';
        profile.x = snapshot.x;
        profile.y = snapshot.y;
        profile.z = snapshot.z;
        profile.magnitude = snapshot.magnitude;
        profile.noise = snapshot.noise;
        profile.tolerance = Clamp(100 + snapshot.noise * 3,
                                  kMinimumTolerance, kMaximumTolerance);
        profile.sample_count = static_cast<uint32_t>(snapshot.window_count);
        if (!replacing) {
            ++profile_count_;
        }
        return replacing ? RegistrationResult::Replaced
                         : RegistrationResult::Registered;
    }

    bool DeleteProfile(uint8_t id)
    {
        if (id == 0 || id > kMaxProfiles) {
            return false;
        }
        for (std::size_t index = 0; index < kMaxProfiles; ++index) {
            if (profiles_[index].valid && profiles_[index].id == id) {
                profiles_[index] = Profile{};
                --profile_count_;
                return true;
            }
        }
        return false;
    }

    Snapshot GetSnapshot(int64_t now_ms) const
    {
        Snapshot snapshot;
        snapshot.profile_count = profile_count_;
        if (!has_data_ || window_count_ == 0) {
            return snapshot;
        }

        snapshot.has_data = true;
        snapshot.sensor_kind = sensor_kind_;
        snapshot.sensor_supported = IsSupportedSensor(sensor_kind_);
        snapshot.window_count = window_count_;
        snapshot.first_sample_ms = first_sample_ms_;
        snapshot.last_sample_ms = last_sample_ms_;
        snapshot.age_ms = now_ms >= last_sample_ms_ ? now_ms - last_sample_ms_ : -1;
        snapshot.span_ms = ComputeStatistics(snapshot);

        if (snapshot.age_ms < 0 || snapshot.age_ms > kStaleAfterMs) {
            snapshot.status = Status::Stale;
            return snapshot;
        }
        if (!snapshot.sensor_supported) {
            snapshot.status = Status::UnsupportedSensor;
            return snapshot;
        }
        if (window_count_ < kMinimumStableSamples ||
            snapshot.span_ms < kMinimumStableSpanMs) {
            snapshot.status = Status::Collecting;
            return snapshot;
        }
        if (snapshot.noise > kStableNoiseLimit) {
            snapshot.status = Status::Unstable;
            return snapshot;
        }

        snapshot.stable = true;
        if (profile_count_ == 0) {
            snapshot.status = Status::NoProfiles;
            return snapshot;
        }

        FindMatch(snapshot);
        if (snapshot.best_profile_id == 0) {
            snapshot.status = Status::Unknown;
        } else {
            const Profile* best = FindProfile(snapshot.best_profile_id);
            const bool within_tolerance = best != nullptr &&
                                          snapshot.distance >= 0 &&
                                          snapshot.distance <= best->tolerance;
            if (!within_tolerance) {
                snapshot.status = Status::Unknown;
            } else if ((snapshot.second_distance >= 0 &&
                        snapshot.second_distance - snapshot.distance <=
                            AmbiguityMargin(*best)) ||
                       Confidence(*best, snapshot.distance) < kMinimumKnownConfidence) {
                snapshot.status = Status::Ambiguous;
            } else {
                snapshot.status = Status::Known;
                snapshot.matched_profile_id = snapshot.best_profile_id;
            }
            if (within_tolerance) {
                snapshot.confidence = Confidence(*best, snapshot.distance);
            }
        }
        return snapshot;
    }

    std::size_t CopyProfiles(Profile* output, std::size_t capacity) const
    {
        if (output == nullptr || capacity == 0) {
            return 0;
        }
        const std::size_t copied = std::min(capacity, profile_count_);
        std::size_t out_index = 0;
        for (std::size_t index = 0; index < kMaxProfiles && out_index < copied; ++index) {
            if (profiles_[index].valid) {
                output[out_index++] = profiles_[index];
            }
        }
        return copied;
    }

    std::size_t ProfileCount() const { return profile_count_; }

private:
    struct Sample {
        int16_t x = 0;
        int16_t y = 0;
        int16_t z = 0;
        int64_t timestamp_ms = 0;
    };

    static bool IsSupportedSensor(BaseMagneticSensorKind kind)
    {
        return kind == BaseMagneticSensorKind::Bmm150 ||
               kind == BaseMagneticSensorKind::Qmc6309;
    }

    static std::size_t NameLength(const char* value)
    {
        if (value == nullptr) {
            return 0;
        }
        std::size_t length = 0;
        while (value[length] != '\0' && length <= kMaxNameBytes) {
            ++length;
        }
        return length;
    }

    static bool IsValidName(const char* value, std::size_t length)
    {
        bool has_non_space = false;
        for (std::size_t index = 0; index < length;) {
            const uint8_t first = static_cast<uint8_t>(value[index]);
            uint32_t codepoint = 0;
            std::size_t continuation_count = 0;
            if (first <= 0x7f) {
                codepoint = first;
            } else if (first >= 0xc2 && first <= 0xdf) {
                codepoint = first & 0x1f;
                continuation_count = 1;
            } else if (first >= 0xe0 && first <= 0xef) {
                codepoint = first & 0x0f;
                continuation_count = 2;
            } else if (first >= 0xf0 && first <= 0xf4) {
                codepoint = first & 0x07;
                continuation_count = 3;
            } else {
                return false;
            }
            if (continuation_count >= length - index) {
                return false;
            }
            for (std::size_t offset = 1; offset <= continuation_count; ++offset) {
                const uint8_t next = static_cast<uint8_t>(value[index + offset]);
                if ((next & 0xc0) != 0x80) {
                    return false;
                }
                codepoint = (codepoint << 6) | (next & 0x3f);
            }
            if ((continuation_count == 1 && codepoint < 0x80) ||
                (continuation_count == 2 && codepoint < 0x800) ||
                (continuation_count == 3 && codepoint < 0x10000) ||
                codepoint > 0x10ffff ||
                (codepoint >= 0xd800 && codepoint <= 0xdfff) ||
                codepoint < 0x20 || (codepoint >= 0x7f && codepoint <= 0x9f)) {
                return false;
            }
            has_non_space = has_non_space || codepoint != 0x20;
            index += continuation_count + 1;
        }
        return has_non_space;
    }

    static int Clamp(int value, int minimum, int maximum)
    {
        return std::max(minimum, std::min(maximum, value));
    }

    static int32_t Magnitude(double x, double y, double z)
    {
        return static_cast<int32_t>(std::lround(std::sqrt(x * x + y * y + z * z)));
    }

    void ResetWindow()
    {
        window_count_ = 0;
        window_next_ = 0;
        has_data_ = false;
        sensor_kind_ = BaseMagneticSensorKind::Unknown;
        first_sample_ms_ = 0;
        last_sample_ms_ = 0;
    }

    int64_t ComputeStatistics(Snapshot& snapshot) const
    {
        double sum_x = 0.0;
        double sum_y = 0.0;
        double sum_z = 0.0;
        double sum_x2 = 0.0;
        double sum_y2 = 0.0;
        double sum_z2 = 0.0;
        int64_t oldest = window_[0].timestamp_ms;
        int64_t newest = window_[0].timestamp_ms;
        for (std::size_t index = 0; index < window_count_; ++index) {
            const Sample& sample = window_[index];
            sum_x += sample.x;
            sum_y += sample.y;
            sum_z += sample.z;
            sum_x2 += static_cast<double>(sample.x) * sample.x;
            sum_y2 += static_cast<double>(sample.y) * sample.y;
            sum_z2 += static_cast<double>(sample.z) * sample.z;
            oldest = std::min(oldest, sample.timestamp_ms);
            newest = std::max(newest, sample.timestamp_ms);
        }
        const double count = static_cast<double>(window_count_);
        const double mean_x = sum_x / count;
        const double mean_y = sum_y / count;
        const double mean_z = sum_z / count;
        const double variance = std::max(0.0,
            ((sum_x2 / count) - mean_x * mean_x +
             (sum_y2 / count) - mean_y * mean_y +
             (sum_z2 / count) - mean_z * mean_z) / 3.0);
        snapshot.x = static_cast<int16_t>(std::lround(mean_x));
        snapshot.y = static_cast<int16_t>(std::lround(mean_y));
        snapshot.z = static_cast<int16_t>(std::lround(mean_z));
        snapshot.magnitude = Magnitude(mean_x, mean_y, mean_z);
        snapshot.noise = static_cast<int>(std::lround(std::sqrt(variance)));
        snapshot.first_sample_ms = oldest;
        snapshot.last_sample_ms = newest;
        return newest - oldest;
    }

    static int MatchingDistance(const Snapshot& snapshot, const Profile& profile)
    {
        const double dx = static_cast<double>(snapshot.x) - profile.x;
        const double dy = static_cast<double>(snapshot.y) - profile.y;
        const double dz = static_cast<double>(snapshot.z) - profile.z;
        const double dm = static_cast<double>(snapshot.magnitude) - profile.magnitude;
        return static_cast<int>(std::lround(std::sqrt(
            0.35 * (dx * dx + dy * dy + dz * dz) + 0.65 * dm * dm)));
    }

    static int Confidence(const Profile& profile, int distance)
    {
        if (distance < 0 || distance > profile.tolerance) {
            return 0;
        }
        return Clamp(100 - (distance * 70 / profile.tolerance), 30, 100);
    }

    static int AmbiguityMargin(const Profile& profile)
    {
        return std::max(25, profile.tolerance * 15 / 100);
    }

    const Profile* FindProfile(uint8_t id) const
    {
        for (std::size_t index = 0; index < kMaxProfiles; ++index) {
            if (profiles_[index].valid && profiles_[index].id == id) {
                return &profiles_[index];
            }
        }
        return nullptr;
    }

    void FindMatch(Snapshot& snapshot) const
    {
        int best_distance = 0x7fffffff;
        int second_distance = 0x7fffffff;
        uint8_t best_id = 0;
        for (std::size_t index = 0; index < kMaxProfiles; ++index) {
            const Profile& profile = profiles_[index];
            if (!profile.valid || profile.sensor_kind != snapshot.sensor_kind) {
                continue;
            }
            const int distance = MatchingDistance(snapshot, profile);
            if (distance < best_distance) {
                second_distance = best_distance;
                best_distance = distance;
                best_id = profile.id;
            } else if (distance < second_distance) {
                second_distance = distance;
            }
        }
        if (best_id != 0) {
            snapshot.best_profile_id = best_id;
            snapshot.distance = best_distance;
            snapshot.second_distance = second_distance == 0x7fffffff
                ? -1 : second_distance;
        }
    }

    Sample window_[kWindowCapacity]{};
    std::size_t window_count_ = 0;
    std::size_t window_next_ = 0;
    bool has_data_ = false;
    BaseMagneticSensorKind sensor_kind_ = BaseMagneticSensorKind::Unknown;
    int64_t first_sample_ms_ = 0;
    int64_t last_sample_ms_ = 0;

    Profile profiles_[kMaxProfiles]{};
    std::size_t profile_count_ = 0;
};

#endif  // BASE_MAGNETIC_RECOGNITION_H
