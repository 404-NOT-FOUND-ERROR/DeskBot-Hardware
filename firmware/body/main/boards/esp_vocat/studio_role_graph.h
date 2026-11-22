#pragma once
#include "expression_emote/emote_assets.h"
#include "studio_role_status.h"
#include <cstddef>
#include <cstdint>

namespace deskbot_role {
constexpr size_t kCoreSemantics = 6, kSemantics = 29;
inline constexpr const char* kSemanticNames[kSemantics] = {
    "neutral", "happy", "sad", "embarrassed", "confused", "sleepy",
    "idle", "listening", "thinking", "surprised", "shocked", "question", "look_around",
    "winking", "beat_swing", "relaxed", "laughing", "funny", "loving", "confident",
    "delicious", "crying", "silly", "angry", "insert", "eat", "look_left", "look_right", "tried"};
struct Clip {
    const uint8_t* data = nullptr;
    size_t bytes = 0;
    uint16_t records = 0;
};
struct Expression {
    Clip clips[3]{};  // Single uses slot 0; composite uses entry/loop/exit.
    uint8_t fps = 20;
    bool composite = false, loop = false;
};
struct Role { char id[65]{}; Expression expressions[kSemantics]{}; };
struct Graph { Role* roles = nullptr; uint8_t count = 0, initial = 0;
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    char sha256[65]{};
#endif
 };
enum class LoadResult { Absent, Applied, Invalid };
// Caller holds the recursive Emote lock; all resource pointers belong to its mount.
LoadResult LoadGraph(emote_handle_t handle, Graph& graph);
void ClearGraph(Graph& graph);

// Pure transition state, shared by the device bridge and native tests.
class Sequence {
public:
    bool Request(const Expression* target) {
        wanted_ = target;
        if (!current_ || phase_ == Phase::Held) return Begin();
        return false;
    }
    bool Finish() {
        switch (phase_) {
            case Phase::Entry: phase_ = current_ == wanted_ ? Phase::Loop : Phase::Exit; return true;
            case Phase::Loop:
                if (current_ != wanted_) phase_ = Phase::Exit;
                return true;
            case Phase::Exit: return Begin();
            case Phase::Single:
                if (current_ != wanted_) return Begin();
                if (current_->loop) return true;
                phase_ = Phase::Held; return false;
            default: return false;
        }
    }
    const Expression* Current() const { return current_; }
    Phase GetPhase() const { return phase_; }
    const Clip* CurrentClip() const {
        if (!current_) return nullptr;
        return &current_->clips[phase_ == Phase::Loop ? 1 : phase_ == Phase::Exit ? 2 : 0];
    }
private:
    bool Begin() {
        current_ = wanted_;
        phase_ = current_ ? (current_->composite ? Phase::Entry : Phase::Single) : Phase::None;
        return current_ != nullptr;
    }
    const Expression *current_ = nullptr, *wanted_ = nullptr;
    Phase phase_ = Phase::None;
};
}  // namespace deskbot_role
