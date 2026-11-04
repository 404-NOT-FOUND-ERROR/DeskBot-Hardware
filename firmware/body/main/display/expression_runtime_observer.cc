#include "expression_runtime_observer.h"

#include <cstdio>
#include <cstring>
#include <esp_attr.h>

namespace {

struct SemanticEntry {
    const char* name;
    ExpressionRuntimeObserver::Semantic semantic;
};

constexpr SemanticEntry kSemanticEntries[] = {
    {"angry", ExpressionRuntimeObserver::Semantic::Angry},
    {"beat_swing", ExpressionRuntimeObserver::Semantic::BeatSwing},
    {"confident", ExpressionRuntimeObserver::Semantic::Confident},
    {"confused", ExpressionRuntimeObserver::Semantic::Confused},
    {"crying", ExpressionRuntimeObserver::Semantic::Crying},
    {"delicious", ExpressionRuntimeObserver::Semantic::Delicious},
    {"embarrassed", ExpressionRuntimeObserver::Semantic::Embarrassed},
    {"eat", ExpressionRuntimeObserver::Semantic::Eat},
    {"funny", ExpressionRuntimeObserver::Semantic::Funny},
    {"happy", ExpressionRuntimeObserver::Semantic::Happy},
    {"idle", ExpressionRuntimeObserver::Semantic::Idle},
    {"insert", ExpressionRuntimeObserver::Semantic::Insert},
    {"laughing", ExpressionRuntimeObserver::Semantic::Laughing},
    {"listen", ExpressionRuntimeObserver::Semantic::Listen},
    {"look_around", ExpressionRuntimeObserver::Semantic::LookAround},
    {"look_left", ExpressionRuntimeObserver::Semantic::LookLeft},
    {"look_right", ExpressionRuntimeObserver::Semantic::LookRight},
    {"loving", ExpressionRuntimeObserver::Semantic::Loving},
    {"neutral", ExpressionRuntimeObserver::Semantic::Neutral},
    {"question", ExpressionRuntimeObserver::Semantic::Question},
    {"relaxed", ExpressionRuntimeObserver::Semantic::Relaxed},
    {"sad", ExpressionRuntimeObserver::Semantic::Sad},
    {"shocked", ExpressionRuntimeObserver::Semantic::Shocked},
    {"silly", ExpressionRuntimeObserver::Semantic::Silly},
    {"sleepy", ExpressionRuntimeObserver::Semantic::Sleepy},
    {"surprised", ExpressionRuntimeObserver::Semantic::Surprised},
    {"thinking", ExpressionRuntimeObserver::Semantic::Thinking},
    {"tried", ExpressionRuntimeObserver::Semantic::Tried},
    {"winking", ExpressionRuntimeObserver::Semantic::Winking},
};

constexpr size_t kSemanticEntryCount =
    sizeof(kSemanticEntries) / sizeof(kSemanticEntries[0]);

void AppendNumber(std::string& output, uint64_t value)
{
    char buffer[21]{};
    std::snprintf(buffer, sizeof(buffer), "%llu",
                  static_cast<unsigned long long>(value));
    output += buffer;
}

std::string BusyResult()
{
    return "{\"ok\":false,\"error\":\"busy\",\"retryable\":true}";
}

}  // namespace

ExpressionRuntimeObserver::ExpressionRuntimeObserver()
{
    portMUX_INITIALIZE(&mux_);
}

namespace {

// File-scope construction keeps the first-use path free of a lazy static
// guard.  The object is still completely inert while the Kconfig option is
// disabled because this translation unit is omitted from the default build.
DRAM_ATTR ExpressionRuntimeObserver g_expression_runtime_observer;

}  // namespace

ExpressionRuntimeObserver& ExpressionRuntimeObserver::GetInstance()
{
    return g_expression_runtime_observer;
}

bool ExpressionRuntimeObserver::TryEnter() const
{
#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    // portMUX_TRY_LOCK is a single bounded acquisition attempt.  The
    // SetEmotion callers are task-context callbacks in this source snapshot;
    // no waiting or retry is permitted here.
    return portTRY_ENTER_CRITICAL(&mux_, portMUX_TRY_LOCK) == pdPASS;
#else
    return false;
#endif
}

void ExpressionRuntimeObserver::Exit() const
{
#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    portEXIT_CRITICAL(&mux_);
#endif
}

bool ExpressionRuntimeObserver::ParseSemantic(const char* input, Semantic* output)
{
    if (input == nullptr || output == nullptr || input[0] == '\0') {
        return false;
    }

    size_t length = 0;
    while (length <= kMaxSemanticBytes && input[length] != '\0') {
        ++length;
    }
    if (length == 0) {
        return false;
    }
    if (length > kMaxSemanticBytes) {
        *output = Semantic::Other;
        return true;
    }

    for (size_t i = 0; i < length; ++i) {
        const unsigned char value = static_cast<unsigned char>(input[i]);
        if (value > 0x7F) {
            *output = Semantic::Other;
            return true;
        }
    }

    for (size_t i = 0; i < kSemanticEntryCount; ++i) {
        const size_t expected = std::strlen(kSemanticEntries[i].name);
        if (expected == length &&
            std::memcmp(input, kSemanticEntries[i].name, length) == 0) {
            *output = kSemanticEntries[i].semantic;
            return true;
        }
    }
    *output = Semantic::Other;
    return true;
}

uint32_t ExpressionRuntimeObserver::Elapsed(uint32_t now_ms,
                                             uint32_t previous_ms)
{
    return now_ms - previous_ms;
}

bool ExpressionRuntimeObserver::Observe(const char* semantic_name,
                                        uint32_t timestamp_ms)
{
#if !CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    (void)semantic_name;
    (void)timestamp_ms;
    return false;
#else
    Semantic semantic = Semantic::Other;
    if (!ParseSemantic(semantic_name, &semantic)) {
        return false;
    }
    if (!TryEnter()) {
        // A failed admission is intentionally not retried.  This counter is
        // updated atomically without taking the busy lock.
        NoteContentionDrop();
        return false;
    }

    if (next_sequence_ > kMaxSequence) {
        if (records_dropped_sequence_exhausted_ != UINT32_MAX) {
            ++records_dropped_sequence_exhausted_;
        }
        Exit();
        return false;
    }

    if (record_count_ > 0) {
        const uint8_t last_index = static_cast<uint8_t>(
            (next_index_ + kCapacity - 1) % kCapacity);
        Record& last = records_[last_index];
        if (last.semantic == semantic &&
            Elapsed(timestamp_ms, last.last_seen_ms) <= kCoalesceWindowMs) {
            last.last_seen_ms = timestamp_ms;
            if (last.repeat_count < kMaxRepeatCount) {
                ++last.repeat_count;
            }
            Exit();
            return true;
        }
    }

    Record record{};
    record.sequence = next_sequence_++;
    record.first_seen_ms = timestamp_ms;
    record.last_seen_ms = timestamp_ms;
    record.repeat_count = 1;
    record.semantic = semantic;
    records_[next_index_] = record;
    next_index_ = static_cast<uint8_t>((next_index_ + 1) % kCapacity);
    if (record_count_ < kCapacity) {
        ++record_count_;
    } else if (records_overwritten_ != UINT32_MAX) {
        ++records_overwritten_;
    }
    Exit();
    return true;
#endif
}

bool ExpressionRuntimeObserver::TryRead(Snapshot* output,
                                        uint32_t captured_at_ms) const
{
    if (output == nullptr) {
        return false;
    }
    if (!TryEnter()) {
        return false;
    }

#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    // Once admitted, writing directly to the caller-owned snapshot keeps the
    // bounded reader from consuming a second full ring-sized stack buffer.
    *output = Snapshot{};
    output->captured_at_ms = captured_at_ms;
    output->records_overwritten = records_overwritten_;
    for (uint8_t core = 0; core < configNUM_CORES; ++core) {
        output->records_dropped_contention +=
            __atomic_load_n(&contention_by_core_[core], __ATOMIC_RELAXED) -
            contention_baseline_[core];
    }
    output->records_dropped_sequence_exhausted =
        records_dropped_sequence_exhausted_;
    output->record_count = record_count_;
    const uint8_t oldest = record_count_ == kCapacity ? next_index_ : 0;
    for (uint8_t i = 0; i < record_count_; ++i) {
        output->records[i] = records_[(oldest + i) % kCapacity];
    }
#else
    (void)captured_at_ms;
#endif

    Exit();
    return true;
}

bool ExpressionRuntimeObserver::Clear()
{
#if !CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    return false;
#else
    if (!TryEnter()) {
        return false;
    }
    for (Record& record : records_) {
        record = Record{};
    }
    records_overwritten_ = 0;
    records_dropped_sequence_exhausted_ = 0;
    record_count_ = 0;
    next_index_ = 0;
    // Reset by sampling each core's total, not writing a competing producer's
    // counter. A concurrent drop is on one side of this bounded sample.
    for (uint8_t core = 0; core < configNUM_CORES; ++core) {
        contention_baseline_[core] =
            __atomic_load_n(&contention_by_core_[core], __ATOMIC_RELAXED);
    }
    Exit();
    return true;
#endif
}

void ExpressionRuntimeObserver::NoteContentionDrop() const
{
    const UBaseType_t previous_mask = portSET_INTERRUPT_MASK_FROM_ISR();
    const BaseType_t core = xPortGetCoreID();
    const uint32_t previous =
        __atomic_load_n(&contention_by_core_[core], __ATOMIC_RELAXED);
    __atomic_store_n(&contention_by_core_[core], previous + 1,
                     __ATOMIC_RELAXED);
    portCLEAR_INTERRUPT_MASK_FROM_ISR(previous_mask);
}

const char* ExpressionRuntimeObserver::SemanticName(Semantic semantic)
{
    for (size_t i = 0; i < kSemanticEntryCount; ++i) {
        if (kSemanticEntries[i].semantic == semantic) {
            return kSemanticEntries[i].name;
        }
    }
    return "other";
}

std::string ExpressionRuntimeObserver::Describe(uint32_t captured_at_ms) const
{
    Snapshot snapshot{};
    if (!TryRead(&snapshot, captured_at_ms)) {
        return BusyResult();
    }

    std::string result;
    result.reserve(4096);
    result = "{\"ok\":true,\"read_only\":true,\"best_effort\":true,\"schema\":\"deskbot.expression.runtime_observation.e1_26/v1\",\"observation_point\":\"EmoteDisplay::SetEmotion\",\"claim_scope\":\"set_emotion_call_only\",\"captured_at_ms\":";
    AppendNumber(result, snapshot.captured_at_ms);
    result += ",\"records_overwritten\":";
    AppendNumber(result, snapshot.records_overwritten);
    result += ",\"records_dropped_contention\":";
    AppendNumber(result, snapshot.records_dropped_contention);
    result += ",\"records_dropped_sequence_exhausted\":";
    AppendNumber(result, snapshot.records_dropped_sequence_exhausted);
    result += ",\"records\":[";
    for (uint8_t i = 0; i < snapshot.record_count; ++i) {
        if (i > 0) result.push_back(',');
        const Record& record = snapshot.records[i];
        result += "{\"sequence\":";
        AppendNumber(result, record.sequence);
        result += ",\"observation\":\"set_emotion_call_entered\",\"semantic\":\"";
        result += SemanticName(record.semantic);
        result += "\",\"first_seen_ms\":";
        AppendNumber(result, record.first_seen_ms);
        result += ",\"last_seen_ms\":";
        AppendNumber(result, record.last_seen_ms);
        result += ",\"repeat_count\":";
        AppendNumber(result, record.repeat_count);
        result += "}";
    }
    result += "]}";
    return result;
}

