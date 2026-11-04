#ifndef DESKBOT_TEST_PORTMACRO_H
#define DESKBOT_TEST_PORTMACRO_H

#include <cstdint>

#include <freertos/FreeRTOS.h>

struct portMUX_TYPE {
    bool locked = false;
};

constexpr BaseType_t portMUX_TRY_LOCK = 0;

extern bool g_deskbot_test_lock_available;
extern int g_deskbot_test_core;
extern uint32_t g_deskbot_test_try_attempts;
extern uint32_t g_deskbot_test_interrupt_masks;
extern uint32_t g_deskbot_test_interrupt_restores;

inline BaseType_t deskbot_test_try_enter(portMUX_TYPE* mux)
{
    ++g_deskbot_test_try_attempts;
    if (!g_deskbot_test_lock_available || mux->locked) {
        return pdFAIL;
    }
    mux->locked = true;
    return pdPASS;
}

inline void deskbot_test_exit(portMUX_TYPE* mux)
{
    mux->locked = false;
}

inline UBaseType_t deskbot_test_mask_interrupts()
{
    ++g_deskbot_test_interrupt_masks;
    return 0;
}

inline void deskbot_test_restore_interrupts(UBaseType_t)
{
    ++g_deskbot_test_interrupt_restores;
}

inline BaseType_t deskbot_test_core_id()
{
    return g_deskbot_test_core;
}

#define portMUX_INITIALIZE(mux) ((mux)->locked = false)
#define portTRY_ENTER_CRITICAL(mux, timeout) \
    (void)(timeout), deskbot_test_try_enter((mux))
#define portEXIT_CRITICAL(mux) deskbot_test_exit((mux))
#define portSET_INTERRUPT_MASK_FROM_ISR() deskbot_test_mask_interrupts()
#define portCLEAR_INTERRUPT_MASK_FROM_ISR(previous) \
    deskbot_test_restore_interrupts((previous))
#define xPortGetCoreID() deskbot_test_core_id()

#endif  // DESKBOT_TEST_PORTMACRO_H
