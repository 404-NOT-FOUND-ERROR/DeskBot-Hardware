#ifndef DESKBOT_HOST_FREERTOS_H
#define DESKBOT_HOST_FREERTOS_H

// Minimal host-only stand-ins for the lock macros used by BaseEventJournal.
// The test is single-threaded; no RTOS behavior is being simulated here.
#define portENTER_CRITICAL(lock) ((void)(lock))
#define portEXIT_CRITICAL(lock) ((void)(lock))

#endif
