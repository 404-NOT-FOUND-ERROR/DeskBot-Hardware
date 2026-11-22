#pragma once
#include <cassert>
#include <mutex>
struct portMUX_TYPE { std::mutex mutex; };
#define portMUX_INITIALIZER_UNLOCKED {}
inline thread_local bool catalog_critical = false;
inline void portENTER_CRITICAL(portMUX_TYPE* mux) {
    assert(!catalog_critical); mux->mutex.lock(); catalog_critical = true;
}
inline void portEXIT_CRITICAL(portMUX_TYPE* mux) {
    assert(catalog_critical); catalog_critical = false; mux->mutex.unlock();
}
