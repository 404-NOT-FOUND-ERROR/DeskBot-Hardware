#include "studio_usb_session.h"
#include "studio_expression_adapter.h"
#include "studio_expression_control.h"
#include "studio_runtime_state.h"
#include "application.h"
#include "board.h"
#include <atomic>
#include <fstream>
#include <iostream>
#include <iterator>
#include <thread>

std::atomic<int64_t> test_us{0};
std::atomic<uint32_t> hash_delay_ms{0};
std::atomic<unsigned> display_calls{0}, scheduled_peak{0}, readiness_reads{0};
int64_t esp_timer_get_time() { return test_us.load(); }
static void App(DeviceState state) { deskbot_studio_runtime::ApplicationTransition transition(state); }
static void Page(deskbot_studio_runtime::Page page) {
    deskbot_studio_runtime::PageTransition transition; transition.SetPage(page);
}
static void Ready() {
    App(kDeviceStateIdle); Page(deskbot_studio_runtime::Page::Home);
    deskbot_studio_runtime::ObservePlayer(deskbot_studio_display::Player::Initialized);
}
static int Race() {
    using namespace deskbot_studio_expression;
    Ready();
    std::thread writer([] {
        for (int i = 0; i < 20000; ++i) {
            App(i % 2 ? kDeviceStateIdle : kDeviceStateSpeaking);
            Page(i % 2 ? deskbot_studio_runtime::Page::Home : deskbot_studio_runtime::Page::Other);
        }
    });
    std::thread reader([] { for (int i = 0; i < 40000; ++i) (void)deskbot_studio_runtime::ReadDisplay(); });
    std::atomic<bool> done{false};
    std::thread owner([&done] { while (!done) Application::GetInstance().Run(); Application::GetInstance().Run(); });
    const auto hooks = AdapterHooks();
    const SessionView view{"device-a", "boot-b", "session-test", 1, 30000, true};
    char wire[1024], reply[1024];
    for (unsigned i = 1; i <= 2000; ++i) {
        hooks.sync(view, 0);
        std::snprintf(wire, sizeof(wire),
            "{\"schema\":\"%s\",\"message_type\":\"request\",\"request_id\":\"r-%u\","
            "\"target_id\":\"device-a\",\"boot_id\":\"boot-b\",\"session_id\":\"session-test\","
            "\"sequence\":%u,\"catalog_id\":\"%s\",\"role_id\":\"%s\","
            "\"issued_at\":\"2026-11-05T00:00:00.000Z\",\"expires_after_ms\":1000,"
            "\"command\":{\"kind\":\"play_expression\",\"semantic_id\":\"neutral\"}}", kSchema, i, i, kCatalog, kRole);
        if (!hooks.submit(reinterpret_cast<const uint8_t*>(wire), std::strlen(wire), view, 0, reply, sizeof(reply))) std::abort();
        (void)Poll(reply, sizeof(reply));
        if (i % 17 == 0) hooks.disconnect();
    }
    done = true; owner.join(); writer.join(); reader.join(); return 0;
}
static int CoreCases() {
    using namespace deskbot_studio_expression;
    Control core;
    const SessionView view{"device-a", "boot-b", "session-test", 1, 30000, true};
    core.Synchronize(view, 0);
    Request request;
    std::strcpy(request.request_id, "pending"); std::strcpy(request.target_id, view.target);
    std::strcpy(request.boot_id, view.boot); std::strcpy(request.session_id, view.session);
    std::strcpy(request.catalog_id, kCatalog); std::strcpy(request.role_id, kRole);
    std::strcpy(request.command_id, "neutral"); request.sequence = 1; request.ttl_ms = 1000;
    uint8_t digest[32]{}; Receipt receipt; uint32_t ticket = 0;
    if (!core.Submit(request, digest, 0, 0, "sleep_unknown", receipt, ticket) || !ticket) return 30;
    const uint32_t first_ticket = ticket;
    for (unsigned i = 2; i <= 72; ++i) {
        Request busy = request; std::snprintf(busy.request_id, sizeof(busy.request_id), "r-%u", i); busy.sequence = i;
        if (!core.Submit(busy, digest, 0, 0, "sleep_unknown", receipt, ticket) || receipt.status != Status::Busy || ticket) return 31;
    }
    if (!core.Submit(request, digest, 0, 0, "sleep_unknown", receipt, ticket) ||
        receipt.status != Status::Duplicate || receipt.original != Status::Accepted) return 32;
    if (core.Begin(first_ticket + 1, 0, "sleep_unknown")) return 33;
    core.Synchronize(view, 1000);
    if (!core.Poll(receipt, 1000) || receipt.status != Status::Expired) return 34;
    if (core.Begin(first_ticket, 1000, "sleep_unknown")) return 35;
    request.sequence = 73; std::strcpy(request.request_id, "after-expiry");
    if (!core.Submit(request, digest, 1000, 1000, "sleep_unknown", receipt, ticket) || receipt.status != Status::Accepted) return 36;
    return 0;
}
int main(int argc, char** argv) {
    if (argc < 2) return 1;
    if (!std::strcmp(argv[1], "race")) return Race();
    if (!std::strcmp(argv[1], "core")) return CoreCases();
    std::ifstream file(argv[argc-1], std::ios::binary);
    const std::string raw((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
    if (argc == 3 && !std::strcmp(argv[1], "decode")) {
        deskbot_studio_expression::Request r;
        if (!deskbot_studio_expression::Decoder(reinterpret_cast<const uint8_t*>(raw.data()), raw.size()).Read(r)) return 2;
        uint8_t material[800]; const auto count = deskbot_studio_expression::FingerprintMaterial(r, material);
        std::cout.write(reinterpret_cast<const char*>(material), count); return 0;
    }
    Ready();
    deskbot_studio_session::Endpoint endpoint;
    const std::string device(32, 'a'), boot(32, 'b');
    if (!endpoint.Initialize(device.c_str(), boot.c_str())) return 3;
    endpoint.SetDisplayProvider(deskbot_studio_runtime::ReadDisplay);
    endpoint.SetExpressionHooks(deskbot_studio_expression::AdapterHooks());
    size_t position = 0, capacity = 1024;
    const auto number = [&raw, &position](size_t size) {
        uint64_t value = 0;
        if (size > raw.size() - position) std::abort();
        for (size_t i = 0; i < size; ++i) value |= static_cast<uint64_t>(static_cast<uint8_t>(raw[position++])) << (8*i);
        return value;
    };
    char reply[1025]{}; reply[1024] = 'z';
    while (position < raw.size()) {
        const char op = raw[position++];
        test_us = static_cast<int64_t>(number(8) * 1000);
        if (op == 'X') endpoint.Disconnect();
        else if (op == 'T') endpoint.Tick(test_us / 1000);
        else if (op == 'U') Application::GetInstance().Run();
        else if (op == 'Z') {
            if (deskbot_studio_expression::Poll(reply, capacity)) std::cout << reply << '\n';
        } else if (op == 'A') App(static_cast<DeviceState>(number(1)));
        else if (op == 'P') Page(static_cast<deskbot_studio_runtime::Page>(number(1)));
        else if (op == 'R') deskbot_studio_runtime::ObservePlayer(static_cast<deskbot_studio_display::Player>(number(1)));
        else if (op == 'H') Board::GetInstance().display.handle = number(1) != 0;
        else if (op == 'C') capacity = number(4);
        else if (op == 'L') hash_delay_ms = number(4);
        else if (op == 'D') {
            const auto size = number(4);
            if (size > raw.size() - position) return 4;
            for (uint64_t i = 0; i < size; ++i)
                if (endpoint.Feed(static_cast<uint8_t>(raw[position++]), test_us / 1000, reply, capacity))
                    std::cout << reply << '\n';
        } else return 5;
        if (reply[1024] != 'z') return 6;
    }
    std::cerr << display_calls << ' ' << scheduled_peak << ' ' << sizeof(deskbot_studio_expression::Control);
    return 0;
}
