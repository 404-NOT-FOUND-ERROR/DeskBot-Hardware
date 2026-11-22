#include "sdkconfig.h"
#if CONFIG_DESKBOT_ROLE_PLAYER_ENABLE
#include "studio_role_player.h"
#include "studio_asset_layout.h"
#include "expression_emote/emote_api.h"
#include <esp_heap_caps.h>
#include <cstring>
#include <new>
#if DESKBOT_LAYERED_FACE
#include "studio_layered_face.inc"
#include "studio_layered_gfx.h"
#include "studio_role_json.h"
#include <mbedtls/sha256.h>
#include <esp_timer.h>
#include <esp_log.h>
#endif

namespace deskbot_role {
namespace {
#if DESKBOT_LAYERED_FACE
struct Layered;
#endif
struct Runtime {
    Graph graph{};
    Sequence sequence{};
    emote_handle_t handle = nullptr;
    Schedule schedule = nullptr;
    uint64_t ticket = 0;
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    uint64_t selection = 0, rendered_selection = 0;
    uint8_t rendered = kNoRole;
    bool sleep_known = false, failed = false;
#endif
    uint16_t cursor = 0;
    uint8_t selected = 0, semantic = 0, blocked = 0;
    bool completed = false, queued = false, paused = false;
#if DESKBOT_LAYERED_FACE
    Layered* face = nullptr;
#endif
};
Runtime* state = nullptr;  // One mounted display; owned under its recursive Emote lock.
uint64_t serial = 0;
struct Lock {
    emote_handle_t handle;
    bool held;
    explicit Lock(emote_handle_t h) : handle(h), held(h && emote_lock(h) == ESP_OK) {}
    ~Lock() { if (held) emote_unlock(handle); }
};
bool Match(emote_handle_t handle) { return state && state->handle == handle; }
gfx_obj_t* Eye(emote_handle_t h) { return emote_get_obj_by_name(h, "eye_anim"); }
bool Overlay(emote_handle_t handle) {
    auto* dialog = emote_get_obj_by_name(handle, "emerg_dlg");
    return dialog && gfx_obj_get_visible(dialog);
}
bool Blocked(const Runtime& runtime) { return runtime.blocked || Overlay(runtime.handle); }
#if DESKBOT_LAYERED_FACE
void Stop(Runtime& runtime);
#include "studio_layered_runtime.inc"
#endif
void Queue(Runtime& runtime) {
    if (runtime.queued || !runtime.schedule) return;
    runtime.queued = true;
    runtime.schedule(runtime.ticket);
}
void Stop(Runtime& runtime) { if (auto* object = Eye(runtime.handle)) gfx_anim_stop(object); }
esp_err_t Start(Runtime& runtime) {
    const auto* expression = runtime.sequence.Current();
    const auto* clip = runtime.sequence.CurrentClip();
    if (!expression || !clip) return ESP_ERR_INVALID_STATE;
    runtime.ticket = ++serial; runtime.queued = false;
    runtime.cursor = 0; runtime.completed = false; runtime.paused = false;
    if (Blocked(runtime)) { Stop(runtime); runtime.paused = true; return ESP_OK; }
    const auto result = deskbot_asset_layout::PlayClip(runtime.handle, clip->data, clip->bytes, clip->records, expression->fps);
    if (result != ESP_OK) {
        Stop(runtime); runtime.sequence = {};
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
        runtime.failed = true;
#endif
    }
    return result;
}
void Pause(Runtime& runtime) {
    if (!runtime.sequence.Current() || runtime.paused) return;
    Stop(runtime); runtime.paused = true;
}
void Resume(Runtime& runtime) {
    if (!runtime.sequence.Current() || Blocked(runtime)) return;
    if (runtime.completed) { Queue(runtime); return; }
    if (!runtime.paused || runtime.sequence.GetPhase() == Phase::Held) return;
    const auto* expression = runtime.sequence.Current();
    const auto* clip = runtime.sequence.CurrentClip();
    if (runtime.cursor >= clip->records - 1) {
        runtime.completed = true; Queue(runtime); return;
    }
    // Start() itself resets the native cursor. Re-open the remaining segment
    // from the next physical record; the next loop always starts at zero.
    const auto result = deskbot_asset_layout::PlayClip(runtime.handle, clip->data, clip->bytes,
        clip->records, expression->fps, runtime.cursor);
    if (result == ESP_OK) runtime.paused = false;
    else {
        Stop(runtime); runtime.sequence = {}; runtime.paused = false;
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
        runtime.failed = true;
#endif
    }
}
void Clear(Runtime& runtime) {
#if DESKBOT_LAYERED_FACE
    if (runtime.face) { ClearFace(runtime); return; }
#endif
    Stop(runtime);
    runtime.sequence = {};
    runtime.cursor = 0; runtime.completed = runtime.queued = runtime.paused = false;
    runtime.ticket = ++serial;
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    runtime.rendered = kNoRole; runtime.rendered_selection = 0;
#endif
}
}  // namespace
LoadResult Configure(emote_handle_t handle, Schedule schedule, uint8_t initial_blocked) {
    Lock lock(handle);
    if (!lock.held) return LoadResult::Invalid;
    constexpr uint8_t mask = static_cast<uint8_t>(Block::Menu) | static_cast<uint8_t>(Block::Listening) |
                             static_cast<uint8_t>(Block::Sleep);
    if (initial_blocked & static_cast<uint8_t>(~mask)) return LoadResult::Invalid;
    Reset(handle);
    if (state || !schedule) return LoadResult::Invalid;
    auto* memory = heap_caps_malloc(sizeof(Runtime), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!memory) return LoadResult::Invalid;
    auto* candidate = new (memory) Runtime{};
    candidate->handle = handle; candidate->schedule = schedule; candidate->blocked = initial_blocked;
#if DESKBOT_LAYERED_FACE
    auto result = LoadFace(*candidate);
    if (result == LoadResult::Absent) result = LoadGraph(handle, candidate->graph);
#else
    const auto result = LoadGraph(handle, candidate->graph);
#endif
    if (result != LoadResult::Applied) { candidate->~Runtime(); heap_caps_free(candidate); return result; }
    candidate->handle = handle; candidate->schedule = schedule; candidate->blocked = initial_blocked;
    candidate->ticket = ++serial; candidate->selected = candidate->graph.initial;
    state = candidate;
    return result;
}
void Reset(emote_handle_t handle) {
    Lock lock(handle);
    if (!lock.held || !Match(handle)) return;
    Clear(*state);
    // Unmount follows Reset. A stopped animation can still render its last
    // mmap-backed frame, so hide it before the resource mapping is released.
    if (auto* object = Eye(handle)) gfx_obj_set_visible(object, false);
    ClearGraph(state->graph);
    state->~Runtime(); heap_caps_free(state); state = nullptr;
}
bool Request(emote_handle_t handle, const char* semantic, esp_err_t& result) {
    Lock lock(handle);
    if (!lock.held || !Match(handle) || !semantic) return false;
#if DESKBOT_LAYERED_FACE
    if (state->face) return FaceRequest(*state, semantic, false, result);
#endif
    size_t index = 0;
    while (index < kSemantics && std::strcmp(semantic, kSemanticNames[index])) ++index;
    if (index == kSemantics || !state->graph.roles[state->selected].expressions[index].clips[0].data) {
        Clear(*state);
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
        if (state->schedule) state->schedule(0);
#endif
        return false;
    }
    state->semantic = static_cast<uint8_t>(index);
    result = ESP_OK;
    if (state->sequence.Request(&state->graph.roles[state->selected].expressions[index])) result = Start(*state);
    return true;
}
bool Replay(emote_handle_t handle, const char* semantic, esp_err_t& result) {
#if DESKBOT_LAYERED_FACE
    Lock lock(handle);
    if (lock.held && Match(handle) && state->face) return FaceRequest(*state, semantic, true, result);
#endif
    return Request(handle, semantic, result);
}
bool Cancel(emote_handle_t handle) {
#if DESKBOT_LAYERED_FACE
    Lock lock(handle);
    if (lock.held && Match(handle) && state->face) {
        auto& face=*state->face;
        face.player.Cancel();face.tick_us=esp_timer_get_time();face.clock_valid=true;
        return true;
    }
#endif
    esp_err_t result = ESP_OK;
    return Request(handle, "neutral", result) && result == ESP_OK;
}
bool Select(emote_handle_t handle, const char* role_id) {
    Lock lock(handle);
    if (!lock.held || !Match(handle) || !role_id) return false;
    size_t i = 0;
    while (i < state->graph.count && std::strcmp(role_id, state->graph.roles[i].id)) ++i;
    if (i == state->graph.count) return false;
#if DESKBOT_LAYERED_FACE
    if (state->face) {
        state->selection = ++serial; state->failed = false;
        if (state->rendered == i) state->rendered_selection = state->selection;
        return AttachFace(*state) == ESP_OK;
    }
#endif
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    const bool same_rendered = i == state->selected && state->rendered == i &&
        state->rendered_selection == state->selection && state->sequence.GetPhase() != Phase::Exit &&
        state->sequence.Current() != nullptr;
    state->selection = ++serial; state->failed = false;
    if (same_rendered) state->rendered_selection = state->selection;
#endif
    state->selected = static_cast<uint8_t>(i);
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    if (same_rendered) return true;
#endif
    if (state->sequence.Request(&state->graph.roles[i].expressions[state->semantic])) return Start(*state) == ESP_OK;
    return true;
}
void SetBlocked(emote_handle_t handle, Block reason, bool value) {
    Lock lock(handle);
    if (!lock.held || !Match(handle)) return;
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    if (reason == Block::Sleep) state->sleep_known = true;
#endif
    const auto bit = static_cast<uint8_t>(reason);
    if (value) state->blocked |= bit;
    else state->blocked &= static_cast<uint8_t>(~bit);
#if DESKBOT_LAYERED_FACE
    if (state->face) { PollFace(*state); return; }
#endif
    if (Blocked(*state)) Pause(*state);
    else Resume(*state);
}
void SyncOwners(emote_handle_t handle, uint8_t owner_blocked) {
    Lock lock(handle);
    if (!lock.held || !Match(handle)) return;
    constexpr uint8_t owners = static_cast<uint8_t>(Block::Menu) | static_cast<uint8_t>(Block::Listening);
    state->blocked = (state->blocked & static_cast<uint8_t>(~owners)) | (owner_blocked & owners);
#if DESKBOT_LAYERED_FACE
    if (state->face) { PollFace(*state); return; }
#endif
    if (Blocked(*state)) Pause(*state);
    else Resume(*state);
}
void OnFrame(emote_handle_t handle, gfx_player_event_t event, const void* object) {
    Lock lock(handle);
#if DESKBOT_LAYERED_FACE
    if (lock.held && Match(handle) && state->face) return;
#endif
    if (!lock.held || !Match(handle) || !state->sequence.Current()) return;
    if (object != Eye(handle)) return;
    if (event == GFX_PLAYER_EVENT_ONE_FRAME_DONE && !state->paused) {
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
        const auto previous_selection = state->rendered_selection;
        // This callback acknowledges frame processing, not physical LCD visibility.
        // Only the selected expression may acknowledge its activation generation;
        // an old role's entry/exit must complete without saving the new target.
        for (uint8_t i = 0; i < state->graph.count; ++i)
            for (size_t j = 0; j < kSemantics; ++j)
                if (state->sequence.Current() == &state->graph.roles[i].expressions[j]) {
                    state->rendered = i;
                    if (i == state->selected && state->sequence.GetPhase() != Phase::Exit) state->rendered_selection = state->selection;
                }
        if (state->schedule && state->rendered_selection != previous_selection)
            state->schedule(0);  // Once per selection, after the frame callback releases GFX.
#endif
        const auto* clip = state->sequence.CurrentClip();
        if (state->cursor < clip->records - 1) ++state->cursor;
    } else if (event == GFX_PLAYER_EVENT_ALL_FRAME_DONE && !state->paused && !state->completed) {
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
        const auto* clip = state->sequence.CurrentClip();
        if (state->cursor < clip->records - 1) {
            // The reviewed GFX branch emits early ALL only on frame preparation
            // failure. Do not advance phases or acknowledge the new role.
            state->failed = true; state->sequence = {};
            if (state->schedule) state->schedule(0);
            return;
        }
#endif
        state->completed = true;
        if (!Blocked(*state)) Queue(*state);
    }
}
void Drain(emote_handle_t handle, uint64_t ticket) {
    Lock lock(handle);
    if (!lock.held || !Match(handle) || ticket != state->ticket || !state->queued) return;
    state->queued = false;
    if (!state->completed || Blocked(*state)) return;
    state->completed = false;
    if (state->sequence.Finish()) Start(*state);
}
void Poll(emote_handle_t handle) {
    Lock lock(handle);
    if (!lock.held || !Match(handle)) return;
#if DESKBOT_LAYERED_FACE
    if (state->face) { PollFace(*state); return; }
#endif
    if (Blocked(*state)) Pause(*state);
    else Resume(*state);
}
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
Details Describe(emote_handle_t handle) {
    Lock lock(handle);
    Details out{};
    if (!lock.held || !Match(handle)) return out;
    out.loaded = true; out.count = state->graph.count; out.initial = state->graph.initial;
    std::strcpy(out.graph_sha256, state->graph.sha256);
    for (uint8_t i = 0; i < out.count; ++i) {
        std::strcpy(out.roles[i], state->graph.roles[i].id);
        for (size_t j = 0; j < kSemantics; ++j)
            if (state->sequence.Current() == &state->graph.roles[i].expressions[j]) out.current = i;
    }
    out.target = state->selected; out.rendered = state->rendered; out.blocked = state->blocked;
    out.phase = state->sequence.GetPhase(); out.selection = state->selection;
#if DESKBOT_LAYERED_FACE
    if (state->face) { out.phase = FacePhase(*state); out.current = 0; }
#endif
    out.rendered_selection = state->rendered_selection;
    out.failed = state->failed;
    out.sleep_known = state->sleep_known; out.asleep = (state->blocked & static_cast<uint8_t>(Block::Sleep)) != 0;
    return out;
}
#endif
Snapshot Read(emote_handle_t handle) {
    Lock lock(handle);
    if (!lock.held || !Match(handle)) return {};
#if DESKBOT_LAYERED_FACE
    if (state->face) return {true, 0, state->blocked, FacePhase(*state), uint16_t(state->face->player.Clock()/40)};
#endif
    return {true, state->selected, state->blocked, state->sequence.GetPhase(), state->cursor};
}
}  // namespace deskbot_role
#endif
