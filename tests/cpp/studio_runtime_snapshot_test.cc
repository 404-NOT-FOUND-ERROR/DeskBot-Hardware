#include "studio_runtime_state.h"
#include <atomic>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <thread>
#include <type_traits>

using namespace deskbot_studio_runtime;
#define REQUIRE(x) do { if (!(x)) { std::fprintf(stderr, "%d: %s\n", __LINE__, #x); return 1; } } while (0)

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const char* name = argv[1];
    if (!std::strcmp(name, "initial")) {
        Store core;
        const auto s = core.Read();
        REQUIRE(s.application.phase == Phase::Unknown && s.page.phase == Phase::Unknown);
        REQUIRE(s.sleep == Sleep::Unknown && !s.controls_available);
        REQUIRE(s.application.revision == 0 && s.page.revision == 0);
    } else if (!std::strcmp(name, "application")) {
        Store core;
        for (int i = kDeviceStateStarting; i <= kDeviceStateFatalError; ++i) {
            auto ticket = core.BeginApplication();
            REQUIRE(core.Read().application.phase == Phase::Transitioning);
            core.EndApplication(ticket, static_cast<DeviceState>(i));
            REQUIRE(core.Read().application.phase == Phase::Settled);
            REQUIRE(core.Read().application.value == i);
        }
    } else if (!std::strcmp(name, "unknown")) {
        Store core;
        core.EndApplication(core.BeginApplication(), kDeviceStateUnknown);
        REQUIRE(core.Read().application.phase == Phase::Unknown);
        REQUIRE(!KnownApplication(static_cast<DeviceState>(15)));
        REQUIRE(core.Read().application.value == 0);
    } else if (!std::strcmp(name, "page")) {
        Store core;
        for (Page value : {Page::Home, Page::Other, Page::Unknown}) {
            auto ticket = core.BeginPage();
            core.EndPage(ticket, value);
            REQUIRE(core.Read().page.phase == (value == Page::Unknown ? Phase::Unknown : Phase::Settled));
            REQUIRE(core.Read().page.value == static_cast<uint8_t>(value));
        }
    } else if (!std::strcmp(name, "transition")) {
        Channel core;
        core.End(core.Begin(), 3, true);
        auto ticket = core.Begin();
        REQUIRE(core.Read().phase == Phase::Transitioning && core.Read().value == 0);
        REQUIRE(core.Read().revision == 2);
        core.End(ticket, 6, true);
        REQUIRE(core.Read().value == 6 && core.Read().phase == Phase::Settled);
    } else if (!std::strcmp(name, "overlap") || !std::strcmp(name, "reverse")) {
        Channel core;
        auto first = core.Begin(), second = core.Begin();
        if (!std::strcmp(name, "reverse")) { auto swap = first; first = second; second = swap; }
        core.End(first, 3, true);
        REQUIRE(core.Read().phase == Phase::Transitioning);
        core.End(second, 6, true);
        REQUIRE(core.Read().phase == Phase::Unknown && core.Read().value == 0);
        core.End(core.Begin(), 3, true);
        REQUIRE(core.Read().phase == Phase::Settled && core.Read().value == 3);
    } else if (!std::strcmp(name, "independent")) {
        Store core;
        auto app = core.BeginApplication(), page = core.BeginPage();
        core.EndApplication(app, kDeviceStateIdle);
        REQUIRE(core.Read().application.phase == Phase::Settled);
        REQUIRE(core.Read().page.phase == Phase::Transitioning);
        core.EndPage(page, Page::Home);
        REQUIRE(core.Read().page.phase == Phase::Settled);
    } else if (!std::strcmp(name, "duplicate")) {
        Channel core;
        auto first = core.Begin();
        core.End(first, 3, true);
        auto next = core.Begin();
        core.End(first, 3, true);
        core.End(next, 3, true);
        REQUIRE(core.Read().phase == Phase::Unknown && core.Begin() == 0);
    } else if (!std::strcmp(name, "foreign")) {
        Channel core;
        core.Begin();
        core.End(100, 3, true);
        REQUIRE(core.Read().phase == Phase::Unknown && core.Begin() == 0);
    } else if (!std::strcmp(name, "capacity")) {
        Channel core;
        uint32_t tickets[4];
        for (auto& ticket : tickets) { ticket = core.Begin(); REQUIRE(ticket != 0); }
        REQUIRE(core.Begin() == 0);
        for (auto ticket : tickets) core.End(ticket, 3, true);
        REQUIRE(core.Read().phase == Phase::Unknown && core.Begin() == 0);
    } else if (!std::strcmp(name, "exhaustion")) {
        Channel core(UINT32_MAX - 1);
        auto ticket = core.Begin();
        REQUIRE(ticket == UINT32_MAX);
        core.End(ticket, 3, true);
        REQUIRE(core.Begin() == 0);
        REQUIRE(core.Read().phase == Phase::Unknown && core.Read().revision == UINT32_MAX);
    } else if (!std::strcmp(name, "read")) {
        Store core;
        core.EndApplication(core.BeginApplication(), kDeviceStateIdle);
        for (int i = 0; i < 10000; ++i) {
            auto s = core.Read();
            REQUIRE(s.application.revision == 1 && s.page.revision == 0);
            REQUIRE(!s.controls_available && s.sleep == Sleep::Unknown);
        }
    } else if (!std::strcmp(name, "adapter")) {
        REQUIRE(Read().application.phase == Phase::Unknown);
        {
            ApplicationTransition app(kDeviceStateIdle);
            REQUIRE(Read().application.phase == Phase::Transitioning);
            {
                PageTransition page;
                page.SetPage(Page::Home);
                REQUIRE(Read().page.phase == Phase::Transitioning);
            }
            REQUIRE(Read().page.phase == Phase::Settled);
        }
        REQUIRE(Read().application.phase == Phase::Settled);
        REQUIRE(Read().application.value == kDeviceStateIdle);
    } else if (!std::strcmp(name, "early_return")) {
        const auto owner = []() { PageTransition page; return; };
        owner();
        REQUIRE(Read().page.phase == Phase::Unknown);
        { PageTransition page; page.SetPage(Page::Other); }
        REQUIRE(Read().page.phase == Phase::Settled);
    } else if (!std::strcmp(name, "concurrent")) {
        std::atomic<bool> failed{false};
        const auto writer = []() {
            for (int i = 0; i < 20000; ++i) {
                ApplicationTransition app(i % 2 ? kDeviceStateIdle : kDeviceStateSpeaking);
                if (i % 17 == 0) std::this_thread::yield();
            }
        };
        const auto reader = [&failed]() {
            uint32_t app_revision = 0, page_revision = 0;
            for (int i = 0; i < 80000; ++i) {
                const auto s = Read();
                if (s.application.revision < app_revision || s.page.revision < page_revision ||
                    s.controls_available || s.sleep != Sleep::Unknown ||
                    (s.application.phase == Phase::Settled && s.application.value != kDeviceStateIdle &&
                     s.application.value != kDeviceStateSpeaking) ||
                    (s.application.phase != Phase::Settled && s.application.value != 0) ||
                    (s.page.phase != Phase::Settled && s.page.value != 0)) failed = true;
                app_revision = s.application.revision;
                page_revision = s.page.revision;
            }
        };
        std::thread a(writer), b(writer), c(writer), r(reader);
        std::thread p([]() {
            for (int i = 0; i < 20000; ++i) { PageTransition page; page.SetPage(Page::Home); }
        });
        a.join(); b.join(); c.join(); r.join(); p.join();
        REQUIRE(!failed);
        { ApplicationTransition app(kDeviceStateIdle); }
        REQUIRE(Read().application.phase == Phase::Settled);
        REQUIRE(Read().application.revision == 60001 && Read().page.revision == 20000);
    } else if (!std::strcmp(name, "bounds")) {
        REQUIRE(sizeof(Store) <= 80 && sizeof(Snapshot) <= 24);
        REQUIRE(!std::is_copy_constructible<ApplicationTransition>::value);
        REQUIRE(!std::is_copy_constructible<PageTransition>::value);
        REQUIRE(std::is_trivially_copyable<Snapshot>::value);
    } else return 2;
    std::puts("passed");
    return 0;
}
