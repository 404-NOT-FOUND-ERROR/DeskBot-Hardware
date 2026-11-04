#ifndef EXPRESSION_RUNTIME_OBSERVER_H
#define EXPRESSION_RUNTIME_OBSERVER_H

#include <cstdint>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>

// Optional, fixed-memory observer for semantic calls entering SetEmotion.
// It does not observe resource resolution, rendering, or motion completion.
class ExpressionRuntimeObserver {
public:
    static constexpr uint8_t kCapacity = 24;
    static constexpr uint8_t kMaxSemanticBytes = 24;
    static constexpr uint32_t kCoalesceWindowMs = 250;
    static constexpr uint16_t kMaxRepeatCount = 65535;
    static constexpr uint64_t kMaxSequence = 9007199254740991ULL;

    enum class Semantic : uint8_t {
        Angry,
        BeatSwing,
        Confident,
        Confused,
        Crying,
        Delicious,
        Embarrassed,
        Eat,
        Funny,
        Happy,
        Idle,
        Insert,
        Laughing,
        Listen,
        LookAround,
        LookLeft,
        LookRight,
        Loving,
        Neutral,
        Question,
        Relaxed,
        Sad,
        Shocked,
        Silly,
        Sleepy,
        Surprised,
        Thinking,
        Tried,
        Winking,
        Other,
    };

    struct Record {
        uint64_t sequence = 0;
        uint32_t first_seen_ms = 0;
        uint32_t last_seen_ms = 0;
        uint16_t repeat_count = 0;
        Semantic semantic = Semantic::Other;
    };

    struct Snapshot {
        uint32_t captured_at_ms = 0;
        uint32_t records_overwritten = 0;
        uint32_t records_dropped_contention = 0;
        uint32_t records_dropped_sequence_exhausted = 0;
        uint8_t record_count = 0;
        Record records[kCapacity]{};  // oldest-to-newest
    };

    // Constructed as a file-scope static object; no lazy first-use guard.
    ExpressionRuntimeObserver();

    static ExpressionRuntimeObserver& GetInstance();

    // Returns false for disabled-by-build, empty input, or one failed lock
    // attempt. The input is reduced to a fixed enum before it is retained.
    bool Observe(const char* semantic_name, uint32_t timestamp_ms);

    // A busy read leaves output untouched. Formatting occurs after the copy.
    bool TryRead(Snapshot* output, uint32_t captured_at_ms) const;
    std::string Describe(uint32_t captured_at_ms) const;

    // Clear visible records and loss counters, preserving this boot's sequence.
    bool Clear();

    static const char* SemanticName(Semantic semantic);
    static bool ParseSemantic(const char* input, Semantic* output);
    static uint32_t Elapsed(uint32_t now_ms, uint32_t previous_ms);

private:
#ifdef DESKBOT_EXPRESSION_OBSERVER_TESTING
    friend struct ExpressionRuntimeObserverTestPeer;
#endif

    bool TryEnter() const;
    void Exit() const;
    void NoteContentionDrop() const;

    mutable portMUX_TYPE mux_{};
    Record records_[kCapacity]{};
    uint64_t next_sequence_ = 1;
    uint32_t records_overwritten_ = 0;
    uint32_t records_dropped_sequence_exhausted_ = 0;
    uint8_t record_count_ = 0;
    uint8_t next_index_ = 0;
    // Each task updates only its current core's counter with local interrupts
    // masked. GCC __atomic load/store builtins provide the aligned 32-bit
    // snapshot without a C++ library atomic or a retry loop on this path.
    mutable volatile uint32_t contention_by_core_[configNUM_CORES]{};
    uint32_t contention_baseline_[configNUM_CORES]{};  // protected by mux_
};

static_assert(sizeof(uint32_t) == 4,
              "Observer contention counter must remain 32-bit");
static_assert(sizeof(ExpressionRuntimeObserver::Record) <= 24,
              "Observer record RAM budget exceeded");
static_assert(sizeof(ExpressionRuntimeObserver::Snapshot) <= 640,
              "Observer snapshot RAM budget exceeded");
static_assert(sizeof(ExpressionRuntimeObserver) <= 768,
              "Observer total RAM budget exceeded");

#endif  // EXPRESSION_RUNTIME_OBSERVER_H
