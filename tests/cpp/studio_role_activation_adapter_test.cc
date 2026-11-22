// Include the unmodified production adapter so its private lock can be probed
// from another native thread. Board, application, player and NVS are the seams.
#include "../../firmware/body/main/boards/esp_vocat/studio_role_activation.cc"
#include <cassert>
#include <cstdlib>
#include <deque>
#include <functional>
#include <iostream>
#include <string>
#include <thread>

namespace test {
using deskbot_role::Details;
using deskbot_role::Phase;
using deskbot_role_activation::Reply;
using deskbot_role_activation::Saved;
using deskbot_role_activation::Session;
struct State {
    Details device{};
    Saved committed{}, pending{};
    std::deque<std::function<void()>> queue;
    std::function<void()> during_commit;
    std::string failure;
    uint64_t now = 10;
    unsigned describe = 0, selects = 0, restarts = 0, probes = 0;
    unsigned reads = 0, sets = 0, commits = 0, opens = 0, closes = 0;
    bool app = true, display = true, has_blob = false, staged = false, allocation_fails = false;
    int open_mode = -1;
} state;

// try_lock runs on a different thread; probing a mutex owned by the calling
// thread itself would not be a portable lock assertion.
void Unlocked() {
    bool acquired = false;
    std::thread probe([&] {
        acquired = deskbot_role_activation::mutex.try_lock();
        if (acquired) deskbot_role_activation::mutex.unlock();
    });
    probe.join();
    assert(acquired);
    ++state.probes;
}
void MainCall() { assert(state.app); Unlocked(); }
void InitDevice() {
    state.device = {};
    state.device.loaded = true;
    state.device.count = 2;
    state.device.initial = state.device.target = 1;
    std::memset(state.device.graph_sha256, 'a', 64);
    std::strcpy(state.device.roles[0], "grok-round-personal-v1");
    std::strcpy(state.device.roles[1], "xiaozhi-e145");
}
Saved Choice(unsigned index) {
    Saved row;
    std::strcpy(row.graph, state.device.graph_sha256);
    std::strcpy(row.role, state.device.roles[index]);
    return row;
}
Session View() { return {"device-a", "boot-b", "session-b-1", 1, 30000, true}; }
std::string Op(unsigned index) { return "role.activate." + std::to_string(index) + "." + std::string(64, 'a'); }
Reply Query() {
    Reply out;
    const auto reads = state.describe, opens = state.opens;
    state.app = false;
    assert(deskbot_role_activation::AdapterHooks().request("role.query", "query-1", View(), state.now, out));
    state.app = true;
    // USB status queries use only the bounded copied snapshot.
    assert(state.describe == reads && state.opens == opens);
    return out;
}
Reply Request(unsigned index, unsigned counter) {
    Reply out;
    const auto reads = state.describe, opens = state.opens;
    state.app = false;
    assert(deskbot_role_activation::AdapterHooks().request(Op(index).c_str(),
           ("click-" + std::to_string(counter)).c_str(), View(), state.now, out));
    state.app = true;
    assert(state.describe == reads && state.opens == opens);
    return out;
}
void Pump() {
    while (!state.queue.empty()) {
        auto work = state.queue.front(); state.queue.pop_front();
        state.app = true; work();
    }
}
void FirstFrame() {
    state.device.current = state.device.rendered = state.device.target;
    state.device.rendered_selection = state.device.selection;
    state.device.phase = state.device.target ? Phase::Held : Phase::Entry;
    deskbot_role_activation::Observe(&state);
}
void Mount() { deskbot_role_activation::Mounted(&state); }
void Ready() {
    InitDevice(); Mount();
    // Mount selects the default but does not manufacture a rendered frame.
    assert(state.device.target == 1 && state.device.rendered == deskbot_role::kNoRole);
    state.device.current = state.device.rendered = 1;
    state.device.rendered_selection = state.device.selection;
    state.device.phase = Phase::Held;
    deskbot_role_activation::Observe(&state);
}
}  // namespace test

void* heap_caps_malloc(size_t size, unsigned caps) {
    assert(caps == (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    return test::state.allocation_fails ? nullptr : std::malloc(size);
}
void heap_caps_free(void* memory) { std::free(memory); }
int64_t esp_timer_get_time() { return static_cast<int64_t>(test::state.now * 1000); }
Application& Application::GetInstance() { static Application app; return app; }
void Application::Schedule(std::function<void()> work) {
    test::Unlocked(); test::state.queue.push_back(std::move(work));
}
Board& Board::GetInstance() { static Board board; return board; }
Display* Board::GetDisplay() {
    test::MainCall();
    static emote::EmoteDisplay display(reinterpret_cast<emote_handle_t>(&test::state));
    return test::state.display ? &display : nullptr;
}
namespace deskbot_role {
Details Describe(emote_handle_t handle) {
    test::MainCall(); ++test::state.describe;
    return handle ? test::state.device : Details{};
}
bool Select(emote_handle_t handle, const char* role) {
    test::MainCall(); assert(handle == reinterpret_cast<emote_handle_t>(&test::state)); ++test::state.selects;
    auto& d = test::state.device;
    unsigned index = 0;
    while (index < d.count && std::strcmp(d.roles[index], role)) ++index;
    if (index == d.count) return false;
    const bool same_frame = d.rendered == index && d.current == index && d.rendered_selection == d.selection;
    d.target = static_cast<uint8_t>(index); ++d.selection; d.failed = false;
    if (same_frame) d.rendered_selection = d.selection;
    else { ++test::state.restarts; d.phase = index ? Phase::Single : Phase::Entry; }
    return true;
}
}  // namespace deskbot_role

esp_err_t nvs_open(const char* name, int mode, nvs_handle_t* handle) {
    test::MainCall(); auto& s = test::state;
    assert(!std::strcmp(name, "studio_role") && s.open_mode == -1);
    assert(mode == NVS_READONLY || mode == NVS_READWRITE); ++s.opens;
    if (mode == NVS_READWRITE && s.failure == "open") return -1;
    s.open_mode = mode; *handle = 7; return ESP_OK;
}
esp_err_t nvs_get_blob(nvs_handle_t handle, const char* key, void* data, size_t* size) {
    test::MainCall(); auto& s = test::state;
    assert(handle == 7 && s.open_mode == NVS_READONLY && !std::strcmp(key, "choice"));
    assert(*size == sizeof(test::Saved)); ++s.reads;
    if (!s.has_blob || s.failure == "get") return -1;
    std::memcpy(data, &s.committed, sizeof(s.committed));
    *size = s.failure == "size" ? sizeof(s.committed) - 1 : sizeof(s.committed); return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t handle, const char* key, const void* data, size_t size) {
    test::MainCall(); auto& s = test::state;
    assert(handle == 7 && s.open_mode == NVS_READWRITE && !std::strcmp(key, "choice"));
    assert(size == sizeof(test::Saved)); ++s.sets;
    if (s.failure == "set") return -1;
    std::memcpy(&s.pending, data, size); s.staged = true; return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t handle) {
    test::MainCall(); auto& s = test::state;
    assert(handle == 7 && s.open_mode == NVS_READWRITE && s.staged); ++s.commits;
    if (s.during_commit) { auto callback = s.during_commit; s.during_commit = {}; callback(); }
    if (s.failure == "commit") return -1;
    s.committed = s.pending; s.has_blob = true; return ESP_OK;
}
void nvs_close(nvs_handle_t handle) {
    test::MainCall(); auto& s = test::state;
    assert(handle == 7 && s.open_mode != -1); ++s.closes;
    s.open_mode = -1; s.staged = false;
}

int main(int argc, char** argv) {
    using namespace test;
    assert(argc == 2); const std::string mode = argv[1];
    if (mode == "restore" || mode == "get-failure" || mode == "blob-size" ||
        mode == "blob-version" || mode == "blob-graph" || mode == "blob-role") {
        InitDevice(); state.committed = Choice(0); state.has_blob = true;
        if (mode == "get-failure") state.failure = "get";
        if (mode == "blob-size") state.failure = "size";
        if (mode == "blob-version") state.committed.version = 2;
        if (mode == "blob-graph") state.committed.graph[0] = 'b';
        if (mode == "blob-role") std::strcpy(state.committed.role, "missing");
        Mount(); const auto out = Query();
        assert(state.device.target == (mode == "restore" ? 0 : 1));
        assert(out.saved == (mode == "restore" ? 0 : deskbot_role::kNoRole));
        assert(std::string(out.save) == (mode == "restore" ? "restored" : "none"));
        assert(state.sets == 0 && state.commits == 0 && state.opens == state.closes);
    } else if (mode == "allocation-failure") {
        InitDevice(); state.allocation_fails = true; Mount(); Reply out;
        assert(!deskbot_role_activation::AdapterHooks().request("role.query", "query-1", View(), 10, out));
        assert(state.selects == 0 && state.queue.empty() && state.sets == 0);
    } else {
        Ready(); const auto initial_selects = state.selects;
        if (mode == "open-failure" || mode == "set-failure" || mode == "commit-failure") {
            state.committed = Choice(1); state.has_blob = true; Mount();
            // Restoring a known choice still requires the player's frame facts.
            FirstFrame();
        }
        const auto accepted = Request(0, 1);
        assert(std::string(accepted.result) == "accepted" && std::string(accepted.status) == "queued");
        assert(state.queue.size() == 1);
        if (mode == "disconnect" || mode == "expiry") {
            if (mode == "disconnect") deskbot_role_activation::AdapterHooks().disconnect();
            else state.now += deskbot_role_activation::kQueueMs;
            Pump(); assert(state.selects == initial_selects && state.sets == 0);
            assert(std::string(Query().status) == "cancelled");
        } else if (mode == "latest") {
            Request(1, 2); Pump();
            assert(state.selects == initial_selects + 1 && state.device.target == 1);
            const auto duplicate = Request(0, 1);
            assert(std::string(duplicate.result) == "duplicate" && std::string(duplicate.status) == "cancelled");
        } else if (mode == "graph-change" || mode == "mount-change" || mode == "missing-display") {
            if (mode == "missing-display") state.display = false;
            else state.device.graph_sha256[0] = 'b';
            if (mode == "mount-change") Mount();
            const auto selects = state.selects; Pump();
            assert(state.selects == selects && state.sets == 0);
            if (mode != "mount-change") assert(std::string(Query().status) == "failed");
        } else {
            Pump(); auto out = Query();
            assert(std::string(out.status) == "switching" && state.sets == 0);
            // A frame for another role or an older generation cannot commit.
            state.device.current = state.device.rendered = 1;
            state.device.rendered_selection = state.device.selection;
            deskbot_role_activation::Observe(&state); assert(state.sets == 0);
            state.device.rendered = 0;
            state.device.rendered_selection = state.device.selection - 1;
            deskbot_role_activation::Observe(&state); assert(state.sets == 0);
            if (mode == "frame-failure" || mode == "interrupted") {
                state.device.failed = mode == "frame-failure";
                state.device.phase = Phase::None; state.device.current = deskbot_role::kNoRole;
                deskbot_role_activation::Observe(&state); out = Query();
                assert(std::string(out.status) == (mode == "frame-failure" ? "failed" : "cancelled"));
                assert(state.sets == 0 && std::string(out.save) == "none");
            } else {
                if (mode == "open-failure") state.failure = "open";
                if (mode == "set-failure") state.failure = "set";
                if (mode == "commit-failure") state.failure = "commit";
                if (mode == "request-during-save") state.during_commit = [] {
                    const auto queued = Request(1, 2);
                    assert(std::string(queued.status) == "queued");
                };
                FirstFrame(); out = Query();
                if (!state.failure.empty()) {
                    assert(std::string(out.status) == "applied" && std::string(out.save) == "failed" && out.saved == 1);
                    assert(!std::strcmp(state.committed.role, state.device.roles[1]));
                    state.failure.clear(); const auto restarts = state.restarts;
                    Request(0, 2); Pump(); out = Query();
                    assert(state.restarts == restarts && std::string(out.save) == "saved" && out.saved == 0);
                } else if (mode == "request-during-save") {
                    assert(std::string(out.status) == "queued" && out.saved == 0);
                    Pump(); assert(std::string(Query().status) == "switching");
                    FirstFrame(); out = Query(); assert(out.saved == 1 && std::string(out.save) == "saved");
                } else {
                    assert(std::string(out.status) == "applied" && std::string(out.save) == "saved" && out.saved == 0);
                    const auto writes = state.sets;
                    deskbot_role_activation::Observe(&state); Request(0, 1); Pump();
                    assert(state.sets == writes && state.commits == 1);
                }
            }
        }
        assert(state.open_mode == -1);
    }
    assert(state.probes > 0);
    std::cout << "adapter_pass " << mode << '\n';
}
