#ifndef BASE_MAGNETIC_OBSERVER_H
#define BASE_MAGNETIC_OBSERVER_H

#include "base_magnetic_recognition.h"

#include "freertos/FreeRTOS.h"
#include "freertos/portmacro.h"

#include <cstddef>
#include <cstdint>

// Fixed-size side channel for magnetic samples.  Observe() is intentionally
// bounded and zero-wait so it can run after the official UART callback without
// changing the parser, UI, audio, or base-control scheduling.
class BaseMagneticObserver {
public:
    enum class QueryResult : uint8_t {
        Ok = 0,
        NoData,
        Busy,
        InvalidArgument,
    };

    BaseMagneticObserver();

    bool Observe(const uint8_t* data, int data_len, int64_t timestamp_ms);

    QueryResult Identify(int64_t now_ms,
                         BaseMagneticRecognition::Snapshot* snapshot) const;
    QueryResult List(BaseMagneticRecognition::Profile* output,
                     std::size_t capacity, std::size_t* count) const;

    // Copies the recognition state without doing floating-point work while
    // the critical section is held.  Snapshot matching is computed on a local
    // copy after the lock is released.
    bool TryRead(int64_t now_ms,
                 BaseMagneticRecognition::Snapshot* snapshot) const;

    bool RegisterCurrent(uint8_t id, const char* name, int64_t now_ms);
    bool DeleteProfile(uint8_t id);
    std::size_t CopyProfiles(BaseMagneticRecognition::Profile* output,
                             std::size_t capacity) const;

private:
    mutable portMUX_TYPE mux_;
    BaseMagneticRecognition recognition_;
};

#endif  // BASE_MAGNETIC_OBSERVER_H
