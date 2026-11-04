#include "expression_runtime_observer.h"

#include <cassert>
#include <cstring>
#include <iostream>
#include <limits>

bool g_deskbot_test_lock_available = true;
int g_deskbot_test_core = 0;
uint32_t g_deskbot_test_try_attempts = 0;
uint32_t g_deskbot_test_interrupt_masks = 0;
uint32_t g_deskbot_test_interrupt_restores = 0;

struct ExpressionRuntimeObserverTestPeer {
    static void SetNextSequence(ExpressionRuntimeObserver& observer,
                                uint64_t sequence)
    {
        observer.next_sequence_ = sequence;
    }

    static void SetRepeatCount(ExpressionRuntimeObserver& observer,
                               uint16_t repeat_count)
    {
        assert(observer.record_count_ > 0);
        const uint8_t last_index = static_cast<uint8_t>(
            (observer.next_index_ + ExpressionRuntimeObserver::kCapacity - 1) %
            ExpressionRuntimeObserver::kCapacity);
        observer.records_[last_index].repeat_count = repeat_count;
    }

    static uint32_t SequenceExhausted(const ExpressionRuntimeObserver& observer)
    {
        return observer.records_dropped_sequence_exhausted_;
    }

    static void SetContentionCount(ExpressionRuntimeObserver& observer,
                                   uint8_t core, uint32_t count)
    {
        assert(core < configNUM_CORES);
        __atomic_store_n(&observer.contention_by_core_[core], count,
                         __ATOMIC_RELAXED);
    }
};

#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE

namespace {

void ResetLock()
{
    g_deskbot_test_lock_available = true;
    g_deskbot_test_core = 0;
    g_deskbot_test_try_attempts = 0;
    g_deskbot_test_interrupt_masks = 0;
    g_deskbot_test_interrupt_restores = 0;
}

void AssertSnapshotUntouched(const ExpressionRuntimeObserver::Snapshot& before,
                             const ExpressionRuntimeObserver::Snapshot& after)
{
    assert(before.captured_at_ms == after.captured_at_ms);
    assert(before.records_overwritten == after.records_overwritten);
    assert(before.records_dropped_contention == after.records_dropped_contention);
    assert(before.records_dropped_sequence_exhausted ==
           after.records_dropped_sequence_exhausted);
    assert(before.record_count == after.record_count);
    for (uint8_t i = 0; i < ExpressionRuntimeObserver::kCapacity; ++i) {
        assert(before.records[i].sequence == after.records[i].sequence);
        assert(before.records[i].first_seen_ms == after.records[i].first_seen_ms);
        assert(before.records[i].last_seen_ms == after.records[i].last_seen_ms);
        assert(before.records[i].repeat_count == after.records[i].repeat_count);
        assert(before.records[i].semantic == after.records[i].semantic);
    }
}

void TestParseAndBasicObservation()
{
    ResetLock();
    ExpressionRuntimeObserver::Semantic semantic{};
    const char* known[] = {
        "angry", "beat_swing", "confident", "confused", "crying",
        "delicious", "embarrassed", "eat", "funny", "happy", "idle",
        "insert", "laughing", "listen", "look_around", "look_left",
        "look_right", "loving", "neutral", "question", "relaxed", "sad",
        "shocked", "silly", "sleepy", "surprised", "thinking", "tried",
        "winking",
    };
    for (const char* name : known) {
        assert(ExpressionRuntimeObserver::ParseSemantic(name, &semantic));
        assert(ExpressionRuntimeObserver::SemanticName(semantic) ==
               std::string(name));
        assert(semantic != ExpressionRuntimeObserver::Semantic::Other);
    }
    assert(ExpressionRuntimeObserver::ParseSemantic("unknown", &semantic));
    assert(semantic == ExpressionRuntimeObserver::Semantic::Other);
    assert(!ExpressionRuntimeObserver::ParseSemantic("", &semantic));
    assert(!ExpressionRuntimeObserver::ParseSemantic(nullptr, &semantic));

    const char long_name[] = "1234567890123456789012345";
    assert(ExpressionRuntimeObserver::ParseSemantic(long_name, &semantic));
    assert(semantic == ExpressionRuntimeObserver::Semantic::Other);
    const char non_ascii[] = {static_cast<char>(0xC3), static_cast<char>(0xA9), '\0'};
    assert(ExpressionRuntimeObserver::ParseSemantic(non_ascii, &semantic));
    assert(semantic == ExpressionRuntimeObserver::Semantic::Other);

    ExpressionRuntimeObserver observer;
    assert(observer.Observe("happy", 10));
    assert(observer.Observe("happy", 260));
    assert(observer.Observe("sad", 261));

    ExpressionRuntimeObserver::Snapshot snapshot{};
    assert(observer.TryRead(&snapshot, 300));
    assert(snapshot.record_count == 2);
    assert(snapshot.records[0].sequence == 1);
    assert(snapshot.records[0].semantic == ExpressionRuntimeObserver::Semantic::Happy);
    assert(snapshot.records[0].repeat_count == 2);
    assert(snapshot.records[0].first_seen_ms == 10);
    assert(snapshot.records[0].last_seen_ms == 260);
    assert(snapshot.records[1].sequence == 2);
    assert(snapshot.records[1].semantic == ExpressionRuntimeObserver::Semantic::Sad);
}

void TestWrapAndCapacity()
{
    ResetLock();
    ExpressionRuntimeObserver observer;
    assert(observer.Observe("happy", std::numeric_limits<uint32_t>::max() - 100));
    assert(observer.Observe("happy", 149));  // exactly 250 ms across wrap
    assert(observer.Observe("sad", 500));
    assert(observer.Observe("happy", 600));

    ExpressionRuntimeObserver::Snapshot snapshot{};
    assert(observer.TryRead(&snapshot, 700));
    assert(snapshot.record_count == 3);
    assert(snapshot.records[0].repeat_count == 2);
    assert(snapshot.records[0].first_seen_ms ==
           std::numeric_limits<uint32_t>::max() - 100);
    assert(snapshot.records[0].last_seen_ms == 149);

    assert(observer.Clear());
    for (uint32_t i = 0; i < ExpressionRuntimeObserver::kCapacity + 1; ++i) {
        assert(observer.Observe(i % 2 == 0 ? "happy" : "sad", i * 1000));
    }
    assert(observer.TryRead(&snapshot, 50000));
    assert(snapshot.record_count == ExpressionRuntimeObserver::kCapacity);
    assert(snapshot.records_overwritten == 1);
    assert(snapshot.records[0].sequence == 5);
    assert(snapshot.records[ExpressionRuntimeObserver::kCapacity - 1].sequence ==
           ExpressionRuntimeObserver::kCapacity + 4);
}

void TestSaturationClearAndSequence()
{
    ResetLock();
    ExpressionRuntimeObserver observer;
    assert(observer.Observe("happy", 1));
    ExpressionRuntimeObserverTestPeer::SetRepeatCount(
        observer, ExpressionRuntimeObserver::kMaxRepeatCount);
    assert(observer.Observe("happy", 2));
    ExpressionRuntimeObserver::Snapshot snapshot{};
    assert(observer.TryRead(&snapshot, 3));
    assert(snapshot.records[0].repeat_count == ExpressionRuntimeObserver::kMaxRepeatCount);

    assert(observer.Clear());
    assert(observer.Observe("happy", 4));
    assert(observer.Clear());
    assert(observer.Observe("sad", 5));
    assert(observer.TryRead(&snapshot, 6));
    assert(snapshot.records[0].sequence == 3);

    assert(observer.Clear());
    ExpressionRuntimeObserverTestPeer::SetNextSequence(
        observer, ExpressionRuntimeObserver::kMaxSequence);
    assert(observer.Observe("shocked", 10));
    assert(!observer.Observe("sad", 1000));
    assert(ExpressionRuntimeObserverTestPeer::SequenceExhausted(observer) == 1);
}

void TestContentionAndReadFailure()
{
    ResetLock();
    ExpressionRuntimeObserver observer;
    ExpressionRuntimeObserver::Snapshot sentinel{};
    sentinel.captured_at_ms = 0xA5A5A5A5;
    sentinel.record_count = 7;
    sentinel.records[0].sequence = 99;
    const auto before = sentinel;

    g_deskbot_test_core = 1;
    g_deskbot_test_lock_available = false;
    assert(!observer.Observe("happy", 1));
    assert(!observer.TryRead(&sentinel, 2));
    AssertSnapshotUntouched(before, sentinel);
    assert(!observer.Clear());
    assert(g_deskbot_test_try_attempts == 3);
    assert(g_deskbot_test_interrupt_masks == 1);
    assert(g_deskbot_test_interrupt_restores == 1);

    g_deskbot_test_lock_available = true;
    g_deskbot_test_core = 0;
    assert(observer.TryRead(&sentinel, 3));
    assert(g_deskbot_test_try_attempts == 4);
    assert(sentinel.records_dropped_contention == 1);
    assert(sentinel.record_count == 0);
    assert(observer.Clear());
    assert(g_deskbot_test_try_attempts == 5);
    assert(observer.TryRead(&sentinel, 4));
    assert(sentinel.records_dropped_contention == 0);
}

void TestPerCoreCounterWrap()
{
    ResetLock();
    ExpressionRuntimeObserver observer;
    ExpressionRuntimeObserverTestPeer::SetContentionCount(
        observer, 1, std::numeric_limits<uint32_t>::max());
    g_deskbot_test_core = 1;
    g_deskbot_test_lock_available = false;
    assert(!observer.Observe("happy", 1));
    assert(g_deskbot_test_try_attempts == 1);
    assert(g_deskbot_test_interrupt_masks == 1);
    assert(g_deskbot_test_interrupt_restores == 1);
    ExpressionRuntimeObserver::Snapshot snapshot{};
    g_deskbot_test_lock_available = true;
    assert(observer.TryRead(&snapshot, 2));
    assert(snapshot.records_dropped_contention == 0);  // uint32 modulo wrap
}

void TestDescriptionDoesNotRetainInputAndIsSchemaValidated()
{
    ResetLock();
    ExpressionRuntimeObserver observer;
    assert(observer.Observe("shocked", 1234));
    assert(observer.Observe("user supplied phrase", 2000));
    const std::string description = observer.Describe(2345);
    assert(description.find("user supplied phrase") == std::string::npos);
    std::cout << "observer_json:" << description << '\n';
}

}  // namespace

#endif  // CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE

int main()
{
#if CONFIG_DESKBOT_EXPRESSION_OBSERVER_ENABLE
    TestParseAndBasicObservation();
    TestWrapAndCapacity();
    TestSaturationClearAndSequence();
    TestContentionAndReadFailure();
    TestPerCoreCounterWrap();
    TestDescriptionDoesNotRetainInputAndIsSchemaValidated();
#else
    auto& observer = ExpressionRuntimeObserver::GetInstance();
    ExpressionRuntimeObserver::Snapshot snapshot{};
    snapshot.captured_at_ms = 77;
    assert(!observer.Observe("happy", 1));
    assert(!observer.TryRead(&snapshot, 2));
    assert(snapshot.captured_at_ms == 77);
    assert(!observer.Clear());
#endif
    std::cout << "expression_runtime_observer_cpp_tests_passed\n";
    return 0;
}
