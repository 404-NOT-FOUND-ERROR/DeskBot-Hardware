#ifndef BASE_EVENT_QUERY_H
#define BASE_EVENT_QUERY_H

#include <cstdint>
#include <string>

#include "base_event_journal.h"

namespace base_event_query {

constexpr size_t kMaxJsonBytes = 4096;

// These results are returned as the text field of the MCP result. They do not
// perform any hardware operation and must be called after a snapshot is copied.
std::string Describe(const BaseEventJournal* journal, uint64_t sampled_at_ms);
std::string Busy();
std::string JournalUnavailable();

}  // namespace base_event_query

#endif  // BASE_EVENT_QUERY_H
