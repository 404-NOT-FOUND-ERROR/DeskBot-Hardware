// Offline adapter for legendtired/grok-bot-study, commit dfe11400a51ae4b36948450e2bf8944251dcfeca.
// Source rights remain as described by its LICENSE and RIGHTS.md.
#include "BotRenderer.h"
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <vector>

namespace {
constexpr int kSize = 360;
constexpr unsigned kSeed = 20261111;
bool positiveInteger(const char* text, int limit, int& result) {
    char* end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (!*text || *end || value < 1 || value > limit) return false;
    result = static_cast<int>(value);
    return true;
}
bool write32(FILE* file, unsigned value) {
    const unsigned char bytes[] = {static_cast<unsigned char>(value),
        static_cast<unsigned char>(value >> 8), static_cast<unsigned char>(value >> 16),
        static_cast<unsigned char>(value >> 24)};
    return std::fwrite(bytes, 1, 4, file) == 4;
}
// These source states replace the head with dots or glyphs. Keep the circular-face set explicit.
bool circularFace(int state) {
    switch (state) {
    case bot::Thinking: case bot::Orbit: case bot::Radar: case bot::Progress:
    case bot::Spawning: case bot::Dictating: case bot::Writing: case bot::Sending:
    case bot::Receiving: case bot::Uploading: case bot::Alerting: case bot::PoweringDown:
        return false;
    default: return state >= 0 && state < 35;
    }
}
}

int main(int argc, char** argv) {
    int count = 0, fps = 0;
    if (argc != 5 || !positiveInteger(argv[2], 192, count) ||
        !positiveInteger(argv[3], 60, fps) || 60 % fps ||
        !circularFace(bot::findState(argv[1]))) {
        std::fprintf(stderr, "usage: grok-round-render STATE FRAMES FPS OUTPUT.gray (circular states only)\n");
        return 2;
    }
    auto motion = std::make_unique<bot::Motion>();
    auto renderer = std::make_unique<bot::Renderer>();
    std::vector<unsigned char> workspace(bot::Renderer::WORK_BYTES), pixels(kSize * kSize), alpha(kSize * kSize);
    motion->reset(kSeed);
    if (!motion->setShape("blob") || !motion->setState(argv[1]) ||
        !renderer->begin(workspace.data()) || !renderer->setSize(kSize, kSize)) return 3;
    bot::ViewConfig view;
    view.width = view.height = kSize;
    view.botSize = 256;
    view.solid = true;
    view.mirrorX = false;
    view.particles = true;
    motion->setDisplaySize(view.botSize);
    FILE* out = std::fopen(argv[4], "wb");
    if (!out) return 4;
    bool valid = std::fwrite("GKR2", 1, 4, out) == 4 && write32(out, kSize) &&
        write32(out, kSize) && write32(out, fps) && write32(out, count);
    // Motion advances at its original 60 Hz; output sampling is independent of wall time.
    for (int frame = 0; valid && frame < count; ++frame) {
        for (int tick = 0; tick < 60 / fps; ++tick) motion->tick();
        valid = motion->shape() == 0 && !motion->overflowCount() &&
            renderer->renderOverlay(*motion, view, pixels.data(), alpha.data()) && !renderer->overflowCount() &&
            std::fwrite(pixels.data(), 1, pixels.size(), out) == pixels.size() &&
            std::fwrite(alpha.data(), 1, alpha.size(), out) == alpha.size();
    }
    if (std::fclose(out)) valid = false;
    if (!valid) { std::remove(argv[4]); return 5; }
    std::printf("{\"state\":\"%s\",\"shape\":\"blob\",\"width\":360,\"height\":360,"
        "\"fps\":%d,\"image_frames\":%d,\"motion_ticks\":%llu,\"seed\":%u,"
        "\"motion_overflows\":%u,\"renderer_overflows\":%u}\n", argv[1], fps, count,
        static_cast<unsigned long long>(motion->frame()), kSeed,
        motion->overflowCount(), renderer->overflowCount());
    return 0;
}
