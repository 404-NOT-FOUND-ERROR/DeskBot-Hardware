#include "base_magnetic_query.h"

#include "base_control.h"
#include "base_magnetic_telemetry.h"

#include <algorithm>
#include <limits>

namespace {

// Keep the MCP response bounded below the usual 3 KiB tool-result budget.
constexpr std::size_t kMaxJsonBytes = 3072;

void AppendUnsigned(std::string& output, uint64_t value)
{
    char buffer[20]{};
    std::size_t cursor = sizeof(buffer);
    do {
        buffer[--cursor] = static_cast<char>('0' + (value % 10));
        value /= 10;
    } while (value != 0);
    output.append(buffer + cursor, sizeof(buffer) - cursor);
}

void AppendSigned(std::string& output, int64_t value)
{
    if (value < 0) {
        output.push_back('-');
        const uint64_t magnitude = static_cast<uint64_t>(-(value + 1)) + 1;
        AppendUnsigned(output, magnitude);
    } else {
        AppendUnsigned(output, static_cast<uint64_t>(value));
    }
}

void AppendJsonString(std::string& output, const char* value,
                      std::size_t max_bytes = std::numeric_limits<std::size_t>::max())
{
    output.push_back('"');
    if (value != nullptr) {
        static constexpr char kHex[] = "0123456789ABCDEF";
        std::size_t copied = 0;
        for (const unsigned char* cursor =
                 reinterpret_cast<const unsigned char*>(value);
             copied < max_bytes && *cursor != '\0'; ++cursor, ++copied) {
            switch (*cursor) {
            case '"': output += "\\\""; break;
            case '\\': output += "\\\\"; break;
            case '\b': output += "\\b"; break;
            case '\f': output += "\\f"; break;
            case '\n': output += "\\n"; break;
            case '\r': output += "\\r"; break;
            case '\t': output += "\\t"; break;
            default:
                if (*cursor < 0x20) {
                    output += "\\u00";
                    output.push_back(kHex[(*cursor >> 4) & 0x0f]);
                    output.push_back(kHex[*cursor & 0x0f]);
                } else {
                    output.push_back(static_cast<char>(*cursor));
                }
                break;
            }
        }
    }
    output.push_back('"');
}

const char* QueryResultName(BaseMagneticObserver::QueryResult result)
{
    switch (result) {
    case BaseMagneticObserver::QueryResult::Ok: return "ok";
    case BaseMagneticObserver::QueryResult::NoData: return "no_data";
    case BaseMagneticObserver::QueryResult::Busy: return "busy";
    case BaseMagneticObserver::QueryResult::InvalidArgument:
        return "invalid_argument";
    }
    return "unknown";
}

const char* StatusName(BaseMagneticRecognition::Status status)
{
    switch (status) {
    case BaseMagneticRecognition::Status::NoData: return "no_data";
    case BaseMagneticRecognition::Status::Collecting: return "collecting";
    case BaseMagneticRecognition::Status::Unstable: return "unstable";
    case BaseMagneticRecognition::Status::Stale: return "stale";
    case BaseMagneticRecognition::Status::NoProfiles: return "no_profiles";
    case BaseMagneticRecognition::Status::Known: return "known";
    case BaseMagneticRecognition::Status::Unknown: return "unknown";
    case BaseMagneticRecognition::Status::Ambiguous: return "ambiguous";
    case BaseMagneticRecognition::Status::UnsupportedSensor:
        return "unsupported_sensor";
    }
    return "unknown";
}

std::string ErrorEnvelope(const char* operation, const char* result,
                          bool retryable)
{
    std::string output;
    output.reserve(256);
    output = "{\"schema_version\":1,\"read_only\":true,\"storage\":\"ram\",\"operation\":";
    AppendJsonString(output, operation);
    output += ",\"ok\":false,\"result\":";
    AppendJsonString(output, result);
    output += ",\"retryable\":";
    output += retryable ? "true}" : "false}";
    return output;
}

bool TooLarge(const std::string& output)
{
    return output.size() > kMaxJsonBytes;
}

}  // namespace

namespace base_magnetic_query {

std::string Identify(const BaseControl* base_control, int64_t now_ms)
{
    if (base_control == nullptr) {
        return ErrorEnvelope("identify", "unavailable", true);
    }

    BaseMagneticRecognition::Snapshot snapshot{};
    const auto result = base_control->IdentifyMagnetic(now_ms, &snapshot);
    if (result == BaseMagneticObserver::QueryResult::Busy) {
        return ErrorEnvelope("identify", QueryResultName(result), true);
    }
    if (result == BaseMagneticObserver::QueryResult::InvalidArgument) {
        return ErrorEnvelope("identify", QueryResultName(result), false);
    }

    std::string output;
    output.reserve(1024);
    output = "{\"schema_version\":1,\"read_only\":true,\"storage\":\"ram\",\"operation\":\"identify\",\"ok\":true,\"result\":";
    AppendJsonString(output, QueryResultName(result));
    output += ",\"status\":";
    AppendJsonString(output, StatusName(snapshot.status));
    output += ",\"sampled_at_ms\":";
    AppendSigned(output, now_ms);
    output += ",\"stable\":";
    output += snapshot.stable ? "true" : "false";
    output += ",\"has_data\":";
    output += snapshot.has_data ? "true" : "false";
    output += ",\"sensor\":";
    if (snapshot.has_data) {
        AppendJsonString(output, BaseMagneticSensorKindName(snapshot.sensor_kind));
    } else {
        output += "null";
    }
    output += ",\"sensor_supported\":";
    output += snapshot.sensor_supported ? "true" : "false";
    output += ",\"window_count\":";
    AppendUnsigned(output, snapshot.window_count);
    output += ",\"age_ms\":";
    if (snapshot.age_ms < 0) {
        output += "null";
    } else {
        AppendSigned(output, snapshot.age_ms);
    }
    output += ",\"span_ms\":";
    AppendSigned(output, snapshot.span_ms);
    output += ",\"x\":";
    AppendSigned(output, snapshot.x);
    output += ",\"y\":";
    AppendSigned(output, snapshot.y);
    output += ",\"z\":";
    AppendSigned(output, snapshot.z);
    output += ",\"magnitude\":";
    AppendSigned(output, snapshot.magnitude);
    output += ",\"noise\":";
    AppendSigned(output, snapshot.noise);
    output += ",\"profile_count\":";
    AppendUnsigned(output, snapshot.profile_count);
    output += ",\"best_profile_id\":";
    AppendUnsigned(output, snapshot.best_profile_id);
    output += ",\"matched_profile_id\":";
    if (snapshot.status == BaseMagneticRecognition::Status::Known &&
        snapshot.matched_profile_id != 0) {
        AppendUnsigned(output, snapshot.matched_profile_id);
    } else {
        output += "null";
    }
    output += ",\"confidence\":";
    AppendSigned(output, snapshot.confidence);
    output += ",\"confidence_kind\":\"heuristic_similarity\",\"confidence_is_probability\":false";
    output += ",\"distance\":";
    if (snapshot.distance < 0) output += "null";
    else AppendSigned(output, snapshot.distance);
    output += ",\"second_distance\":";
    if (snapshot.second_distance < 0) output += "null";
    else AppendSigned(output, snapshot.second_distance);
    output += "}";
    if (TooLarge(output)) return ErrorEnvelope("identify", "output_too_large", false);
    return output;
}

std::string List(const BaseControl* base_control)
{
    if (base_control == nullptr) {
        return ErrorEnvelope("list", "unavailable", true);
    }

    BaseMagneticRecognition::Profile profiles[
        BaseMagneticRecognition::kMaxProfiles]{};
    std::size_t count = 0;
    const auto result = base_control->ListMagneticProfiles(
        profiles, BaseMagneticRecognition::kMaxProfiles, &count);
    if (result == BaseMagneticObserver::QueryResult::Busy) {
        return ErrorEnvelope("list", QueryResultName(result), true);
    }
    if (result == BaseMagneticObserver::QueryResult::InvalidArgument) {
        return ErrorEnvelope("list", QueryResultName(result), false);
    }

    std::string output;
    output.reserve(1024);
    output = "{\"schema_version\":1,\"read_only\":true,\"storage\":\"ram\",\"operation\":\"list\",\"ok\":true,\"result\":";
    AppendJsonString(output, count == 0 ? "no_profiles" : QueryResultName(result));
    output += ",\"count\":";
    AppendUnsigned(output, count);
    output += ",\"capacity\":";
    AppendUnsigned(output, BaseMagneticRecognition::kMaxProfiles);
    output += ",\"profiles\":[";
    for (std::size_t index = 0; index < count; ++index) {
        if (index != 0) output.push_back(',');
        const auto& profile = profiles[index];
        output += "{\"id\":";
        AppendUnsigned(output, profile.id);
        output += ",\"name\":";
        AppendJsonString(output, profile.name,
                         BaseMagneticRecognition::kMaxNameBytes);
        output += ",\"sensor\":";
        AppendJsonString(output, BaseMagneticSensorKindName(profile.sensor_kind));
        output += ",\"x\":";
        AppendSigned(output, profile.x);
        output += ",\"y\":";
        AppendSigned(output, profile.y);
        output += ",\"z\":";
        AppendSigned(output, profile.z);
        output += ",\"magnitude\":";
        AppendSigned(output, profile.magnitude);
        output += ",\"noise\":";
        AppendSigned(output, profile.noise);
        output += ",\"tolerance\":";
        AppendSigned(output, profile.tolerance);
        output += ",\"sample_count\":";
        AppendUnsigned(output, profile.sample_count);
        output += "}";
    }
    output += "]}";
    if (TooLarge(output)) return ErrorEnvelope("list", "output_too_large", false);
    return output;
}

}  // namespace base_magnetic_query
