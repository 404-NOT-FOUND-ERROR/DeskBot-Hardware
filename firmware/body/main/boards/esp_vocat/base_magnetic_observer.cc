#include "base_magnetic_observer.h"

#include "base_magnetic_telemetry.h"

#include <algorithm>

BaseMagneticObserver::BaseMagneticObserver()
    : mux_(portMUX_INITIALIZER_UNLOCKED), recognition_()
{
}

bool BaseMagneticObserver::Observe(const uint8_t* data, int data_len,
                                   int64_t timestamp_ms)
{
    BaseMagneticSample sample{};
    if (!DecodeBaseMagneticSample(data, data_len, &sample) ||
        timestamp_ms < 0) {
        return false;
    }
    if (portTRY_ENTER_CRITICAL(&mux_, 0) != pdPASS) {
        return false;
    }
    const bool accepted = recognition_.AddSample(sample, timestamp_ms);
    portEXIT_CRITICAL(&mux_);
    return accepted;
}

bool BaseMagneticObserver::TryRead(
    int64_t now_ms, BaseMagneticRecognition::Snapshot* snapshot) const
{
    if (snapshot == nullptr || now_ms < 0) {
        return false;
    }

    BaseMagneticRecognition copy;
    if (portTRY_ENTER_CRITICAL(&mux_, 0) != pdPASS) {
        return false;
    }
    copy = recognition_;
    portEXIT_CRITICAL(&mux_);

    *snapshot = copy.GetSnapshot(now_ms);
    return true;
}

BaseMagneticObserver::QueryResult BaseMagneticObserver::Identify(
    int64_t now_ms, BaseMagneticRecognition::Snapshot* snapshot) const
{
    if (snapshot == nullptr || now_ms < 0) {
        return QueryResult::InvalidArgument;
    }
    *snapshot = BaseMagneticRecognition::Snapshot{};
    if (!TryRead(now_ms, snapshot)) {
        return QueryResult::Busy;
    }
    return snapshot->has_data ? QueryResult::Ok : QueryResult::NoData;
}

BaseMagneticObserver::QueryResult BaseMagneticObserver::List(
    BaseMagneticRecognition::Profile* output, std::size_t capacity,
    std::size_t* count) const
{
    if (count == nullptr) {
        return QueryResult::InvalidArgument;
    }
    *count = 0;
    if (output == nullptr || capacity == 0) {
        return QueryResult::InvalidArgument;
    }

    BaseMagneticRecognition copy;
    if (portTRY_ENTER_CRITICAL(&mux_, 0) != pdPASS) {
        return QueryResult::Busy;
    }
    copy = recognition_;
    portEXIT_CRITICAL(&mux_);

    *count = copy.CopyProfiles(
        output, std::min(capacity, BaseMagneticRecognition::kMaxProfiles));
    return QueryResult::Ok;
}

bool BaseMagneticObserver::RegisterCurrent(uint8_t id, const char* name,
                                            int64_t now_ms)
{
    if (now_ms < 0 || portTRY_ENTER_CRITICAL(&mux_, 0) != pdPASS) {
        return false;
    }
    const auto result = recognition_.RegisterCurrent(id, name, now_ms);
    portEXIT_CRITICAL(&mux_);
    return result == BaseMagneticRecognition::RegistrationResult::Registered ||
           result == BaseMagneticRecognition::RegistrationResult::Replaced;
}

bool BaseMagneticObserver::DeleteProfile(uint8_t id)
{
    if (portTRY_ENTER_CRITICAL(&mux_, 0) != pdPASS) {
        return false;
    }
    const bool deleted = recognition_.DeleteProfile(id);
    portEXIT_CRITICAL(&mux_);
    return deleted;
}

std::size_t BaseMagneticObserver::CopyProfiles(
    BaseMagneticRecognition::Profile* output, std::size_t capacity) const
{
    BaseMagneticRecognition copy;
    if (portTRY_ENTER_CRITICAL(&mux_, 0) != pdPASS) {
        return 0;
    }
    copy = recognition_;
    portEXIT_CRITICAL(&mux_);
    return copy.CopyProfiles(output, capacity);
}
