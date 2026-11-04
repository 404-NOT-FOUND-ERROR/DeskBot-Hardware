#include "sdkconfig.h"

#if CONFIG_DESKBOT_STUDIO_RUNTIME_SNAPSHOT_ENABLE
#include "studio_runtime_state.h"
#include <freertos/FreeRTOS.h>
#include <freertos/portmacro.h>

namespace deskbot_studio_runtime {
namespace {
Store store;
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
deskbot_studio_display::Store player_store;
#endif
portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

// Critical sections contain only bounded scalar copies/updates. The owner
// releases this lock before its existing display, event, audio or LVGL calls.
class Guard {
public:
    Guard() { portENTER_CRITICAL(&mux); }
    ~Guard() { portEXIT_CRITICAL(&mux); }
    Guard(const Guard&) = delete;
    Guard& operator=(const Guard&) = delete;
};
}  // namespace

Snapshot Read() {
    Guard guard;
    return store.Read();
}

#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
void ObservePlayer(deskbot_studio_display::Player player) {
    Guard guard;
    player_store.Observe(player);
}

deskbot_studio_display::Snapshot ReadDisplay() {
    Guard guard;
    return {store.Read(), player_store.Read()};
}
#endif

ApplicationTransition::ApplicationTransition(DeviceState state) : state_(state) {
    Guard guard;
    ticket_ = store.BeginApplication();
}

ApplicationTransition::~ApplicationTransition() {
    Guard guard;
    store.EndApplication(ticket_, state_);
}

PageTransition::PageTransition() {
    Guard guard;
    ticket_ = store.BeginPage();
}

PageTransition::~PageTransition() {
    Guard guard;
    store.EndPage(ticket_, page_);
}
}  // namespace deskbot_studio_runtime
#endif
