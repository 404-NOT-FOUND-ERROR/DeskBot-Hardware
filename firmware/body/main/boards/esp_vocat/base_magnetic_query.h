#ifndef BASE_MAGNETIC_QUERY_H
#define BASE_MAGNETIC_QUERY_H

#include <cstdint>
#include <string>

class BaseControl;

// Read-only serialization for the RAM magnetic-recognition registry.  The
// BaseControl methods copy bounded state before these functions format JSON,
// so no critical section is held while building the response.
namespace base_magnetic_query {

constexpr uint32_t kSchemaVersion = 1;

std::string Identify(const BaseControl* base_control, int64_t now_ms);
std::string List(const BaseControl* base_control);

}  // namespace base_magnetic_query

#endif  // BASE_MAGNETIC_QUERY_H
