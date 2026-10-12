#ifndef DESKBOT_HOST_SEMPHR_H
#define DESKBOT_HOST_SEMPHR_H

#include <cstdint>

struct deskbot_host_mutex {};
using SemaphoreHandle_t = deskbot_host_mutex*;
using TickType_t = uint32_t;

#ifndef pdTRUE
#define pdTRUE 1
#endif
#ifndef pdFALSE
#define pdFALSE 0
#endif
#ifndef pdMS_TO_TICKS
#define pdMS_TO_TICKS(value) (value)
#endif

inline SemaphoreHandle_t xSemaphoreCreateMutex()
{
    return new deskbot_host_mutex();
}

inline int xSemaphoreTake(SemaphoreHandle_t mutex, TickType_t)
{
    return mutex != nullptr ? pdTRUE : pdFALSE;
}

inline int xSemaphoreGive(SemaphoreHandle_t mutex)
{
    return mutex != nullptr ? pdTRUE : pdFALSE;
}

inline void vSemaphoreDelete(SemaphoreHandle_t mutex)
{
    delete mutex;
}

#endif
