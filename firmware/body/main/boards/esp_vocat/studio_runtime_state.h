#pragma once

#include "studio_runtime_snapshot.h"
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
#include "studio_display_readiness.h"
#endif

namespace deskbot_studio_runtime {
// Reads only the mirrored fixed-size store; no Application/LVGL object access.
Snapshot Read();
#if CONFIG_DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE
void ObservePlayer(deskbot_studio_display::Player player);
deskbot_studio_display::Snapshot ReadDisplay();
#endif

class ApplicationTransition {
public:
    explicit ApplicationTransition(DeviceState state);
    ~ApplicationTransition();
    ApplicationTransition(const ApplicationTransition&) = delete;
    ApplicationTransition& operator=(const ApplicationTransition&) = delete;
private:
    uint32_t ticket_;
    DeviceState state_;
};

class PageTransition {
public:
    PageTransition();
    ~PageTransition();
    void SetPage(Page page) { page_ = page; }
    PageTransition(const PageTransition&) = delete;
    PageTransition& operator=(const PageTransition&) = delete;
private:
    uint32_t ticket_;
    Page page_ = Page::Unknown;
};
}  // namespace deskbot_studio_runtime
