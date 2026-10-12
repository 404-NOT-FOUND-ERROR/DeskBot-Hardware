#include "base_event_journal.h"

#include <cassert>
#include <cstring>
#include <string>
#include <vector>

namespace {

void expect_event(const BaseEventJournal::Event& event, uint32_t sequence,
                  const char* type)
{
    assert(event.sequence == sequence);
    assert(event.type == type);
}

}  // namespace

int main()
{
    BaseEventJournal journal;
    const uint8_t connected_data[] = {0x00, 0x01};
    journal.Record("base.link.connected", 0x00, connected_data,
                   sizeof(connected_data), 100, 0x0001, true);
    journal.Record("base.mechanical.status", 0x05, connected_data,
                   sizeof(connected_data), 200, 0x0001, true);

    auto events = journal.Snapshot();
    assert(events.size() == 2);
    expect_event(events[0], 1, "base.link.connected");
    expect_event(events[1], 2, "base.mechanical.status");
    assert(events[0].command == 0x00);
    assert(events[0].value == 0x0001 && events[0].has_value);
    assert(events[0].data_length == sizeof(connected_data));
    assert(events[0].data[0] == 0x00 && events[0].data[1] == 0x01);
    assert(events[0].timestamp_ms == 100);
    assert(events[0].frame_hex == "AA55000300000101");

    // A synthetic disconnect has no fabricated receive frame.
    journal.RecordSynthetic("base.link.disconnected", 0x00, 300);
    events = journal.Snapshot();
    assert(events.size() == 3);
    expect_event(events.back(), 3, "base.link.disconnected");
    assert(events.back().synthetic);
    assert(events.back().data_length == 0);
    assert(events.back().frame_hex.empty());

    // UART-sized payloads are bounded to the protocol maximum in the journal.
    std::vector<uint8_t> long_data(BaseEventJournal::kMaxDataBytes + 1);
    for (size_t i = 0; i < long_data.size(); ++i) {
        long_data[i] = static_cast<uint8_t>(i);
    }
    journal.Record("base.uart.unknown_event", 0x7F, long_data.data(),
                   static_cast<int>(long_data.size()), 400);
    events = journal.Snapshot();
    assert(events.back().data_length == BaseEventJournal::kMaxDataBytes);
    assert(events.back().frame_hex.size() == (2 + 2 + 1 + BaseEventJournal::kMaxDataBytes + 1) * 2);

    // Ring capacity is fixed and sequence IDs remain monotonic after wrap.
    for (size_t i = 0; i < BaseEventJournal::kCapacity + 3; ++i) {
        journal.Record("base.uart.unknown_event", 0x7E, nullptr, 0,
                       500 + static_cast<int64_t>(i));
    }
    events = journal.Snapshot();
    assert(events.size() == BaseEventJournal::kCapacity);
    const uint32_t newest_sequence = journal.TotalRecorded();
    assert(events.front().sequence == newest_sequence - BaseEventJournal::kCapacity + 1);
    assert(events.back().sequence == newest_sequence);
    expect_event(events.back(), newest_sequence, "base.uart.unknown_event");
    return 0;
}
