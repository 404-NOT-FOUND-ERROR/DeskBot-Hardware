#ifndef DESKBOT_TEST_FREERTOS_H
#define DESKBOT_TEST_FREERTOS_H

#include <cstdint>

using BaseType_t = int;
using UBaseType_t = unsigned int;

constexpr BaseType_t pdPASS = 1;
constexpr BaseType_t pdFAIL = 0;
constexpr unsigned int configNUM_CORES = 2;

#endif  // DESKBOT_TEST_FREERTOS_H
