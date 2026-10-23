#include "base_event_query.h"

#include <cstdio>

namespace {

void AppendNumber(std::string& output, uint64_t value)
{
    char buffer[21]{};
    std::snprintf(buffer, sizeof(buffer), "%llu",
                  static_cast<unsigned long long>(value));
    output += buffer;
}

void AppendHex(std::string& output, const uint8_t* bytes, uint8_t length)
{
    static constexpr char kHex[] = "0123456789ABCDEF";
    for (uint8_t i = 0; i < length; ++i) {
        output.push_back(kHex[(bytes[i] >> 4) & 0x0F]);
        output.push_back(kHex[bytes[i] & 0x0F]);
    }
}

std::string Error(const char* error, bool retryable)
{
    std::string result = "{\"ok\":false,\"error\":\"";
    result += error;
    result += "\",\"retryable\":";
    result += retryable ? "true}" : "false}";
    return result;
}

}  // namespace

namespace base_event_query {

std::string Busy()
{
    return Error("busy", true);
}

std::string JournalUnavailable()
{
    return Error("journal_unavailable", true);
}

std::string Describe(const BaseEventJournal* journal, uint64_t sampled_at_ms)
{
    if (journal == nullptr) return JournalUnavailable();
    BaseEventJournal::RecentSnapshot snapshot{};
    if (!journal->TryReadRecent(&snapshot)) return Busy();

    std::string result;
    result.reserve(kMaxJsonBytes);
    result = "{\"read_only\":true,\"best_effort\":true,\"sampled_at_ms\":";
    AppendNumber(result, sampled_at_ms);
    result += ",\"online\":";
    result += snapshot.state.online ? "true" : "false";
    result += ",\"heartbeat_valid\":";
    result += snapshot.state.heartbeat_valid ? "true" : "false";
    result += ",\"last_heartbeat_ms\":";
    if (snapshot.state.heartbeat_valid) {
        AppendNumber(result, snapshot.state.last_heartbeat_ms);
    } else {
        result += "null";
    }
    result += ",\"mechanical_status\":";
    if (snapshot.state.mechanical_status_valid) {
        AppendNumber(result, snapshot.state.mechanical_status);
    } else {
        result += "null";
    }
    result += ",\"mechanical_status_name\":";
    if (snapshot.state.mechanical_status_valid) {
        result.push_back('"');
        result += base_event_mapper::MechanicalStatusName(
            snapshot.state.mechanical_status);
        result.push_back('"');
    } else {
        result += "null";
    }
    result += ",\"retained_count\":";
    AppendNumber(result, snapshot.retained_count);
    result += ",\"returned_count\":";
    AppendNumber(result, snapshot.returned_count);
    result += ",\"has_more\":";
    result += snapshot.retained_count > snapshot.returned_count ? "true" : "false";
    result += ",\"total_recorded\":";
    AppendNumber(result, snapshot.state.total_recorded);
    result += ",\"events\":[";

    for (uint8_t i = 0; i < snapshot.returned_count; ++i) {
        const auto& event = snapshot.events[i];
        if (i > 0) result.push_back(',');
        result += "{\"sequence\":";
        AppendNumber(result, event.sequence);
        result += ",\"timestamp_ms\":";
        AppendNumber(result, event.timestamp_ms);
        result += ",\"type\":\"";
        result += base_event_mapper::EventName(event.type, event.value);
        result += "\",\"command\":";
        if ((event.flags & BaseEventJournal::kFlagSynthetic) != 0) {
            result += "null";
        } else {
            AppendNumber(result, event.command);
        }
        result += ",\"value\":";
        if ((event.flags & BaseEventJournal::kFlagHasValue) != 0) {
            AppendNumber(result, event.value);
        } else {
            result += "null";
        }
        result += ",\"flags\":";
        AppendNumber(result, event.flags);
        result += ",\"data_length\":";
        AppendNumber(result, event.data_length);
        result += ",\"stored_length\":";
        AppendNumber(result, event.stored_length);
        result += ",\"data_hex\":\"";
        AppendHex(result, event.data, event.stored_length);
        result += "\",\"frame_hex\":";
        if (event.frame_length == 0) {
            result += "null";
        } else {
            result.push_back('"');
            AppendHex(result, event.frame, event.frame_length);
            result.push_back('"');
        }
        result += ",\"checksum\":";
        if ((event.flags & BaseEventJournal::kFlagSynthetic) != 0) {
            result += "null";
        } else {
            AppendNumber(result, event.checksum);
        }
        result += ",\"synthetic\":";
        result += (event.flags & BaseEventJournal::kFlagSynthetic) != 0 ? "true" : "false";
        result += ",\"data_truncated\":";
        result += (event.flags & BaseEventJournal::kFlagDataTruncated) != 0 ? "true" : "false";
        result += ",\"known\":";
        result += (event.flags & BaseEventJournal::kFlagKnown) != 0 ? "true}" : "false}";
        if (result.size() > kMaxJsonBytes) return Error("output_too_large", false);
    }

    result += "]}";
    if (result.size() > kMaxJsonBytes) return Error("output_too_large", false);
    return result;
}

}  // namespace base_event_query
