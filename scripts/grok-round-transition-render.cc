// Continuous desktop reference; source notices are retained with the circular library.
#include "BotRenderer.h"
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

namespace {
constexpr int kSize = 360;
constexpr unsigned kSeed = 20261111;
struct Segment { std::string state; int frames, start; };
bool positive(const char* text, int maximum, int& value) {
    char* end = nullptr;
    const long number = std::strtol(text, &end, 10);
    if (!*text || *end || number < 1 || number > maximum) return false;
    value = static_cast<int>(number);
    return true;
}
bool allowed(const std::string& name) {
    for (const char* state : {"idle", "happy", "sad", "shy", "confused", "drowsy"})
        if (name == state) return true;
    return false;
}
bool write32(FILE* file, unsigned value) {
    const unsigned char bytes[] = {static_cast<unsigned char>(value),
        static_cast<unsigned char>(value >> 8), static_cast<unsigned char>(value >> 16),
        static_cast<unsigned char>(value >> 24)};
    return std::fwrite(bytes, 1, 4, file) == 4;
}
}

int main(int argc, char** argv) {
    int fps = 0, total = 0;
    if (argc < 4 || argc > 19 || !positive(argv[1], 60, fps) || 60 % fps) return 2;
    std::vector<Segment> segments;
    // Validate the complete schedule before creating the output.
    for (int i = 3; i < argc; ++i) {
        const std::string token(argv[i]);
        const auto colon = token.find(':');
        int frames = 0;
        if (colon == std::string::npos || !allowed(token.substr(0, colon)) ||
            !positive(token.c_str() + colon + 1, 192, frames) || total + frames > 192) return 2;
        segments.push_back({token.substr(0, colon), frames, total});
        total += frames;
    }
    auto motion = std::make_unique<bot::Motion>();
    auto renderer = std::make_unique<bot::Renderer>();
    std::vector<unsigned char> workspace(bot::Renderer::WORK_BYTES), pixels(kSize * kSize), alpha(kSize * kSize);
    motion->reset(kSeed);  // One reset for the entire timeline; never reset at a state boundary.
    if (!motion->setShape("blob") || !renderer->begin(workspace.data()) || !renderer->setSize(kSize, kSize)) return 3;
    motion->setSmoothPresentation(true);  // Eye size/gap ease alongside the original eye contours.
    bot::ViewConfig view;
    view.width = view.height = kSize;
    view.botSize = 256;
    view.solid = true;
    view.mirrorX = false;
    view.particles = true;
    motion->setDisplaySize(view.botSize);
    FILE* out = std::fopen(argv[2], "wb");
    if (!out) return 4;
    bool valid = std::fwrite("GKR2", 1, 4, out) == 4 && write32(out, kSize) &&
        write32(out, kSize) && write32(out, fps) && write32(out, total);
    for (const auto& segment : segments) {
        if (!valid || !motion->setState(segment.state.c_str())) { valid = false; break; }
        for (int frame = 0; valid && frame < segment.frames; ++frame) {
            for (int tick = 0; tick < 60 / fps; ++tick) motion->tick();
            valid = motion->shape() == 0 && !motion->overflowCount() &&
                renderer->renderOverlay(*motion, view, pixels.data(), alpha.data()) && !renderer->overflowCount() &&
                std::fwrite(pixels.data(), 1, pixels.size(), out) == pixels.size() &&
                std::fwrite(alpha.data(), 1, alpha.size(), out) == alpha.size();
        }
    }
    if (std::fclose(out)) valid = false;
    if (!valid) { std::remove(argv[2]); return 5; }
    std::printf("{\"shape\":\"blob\",\"fps\":%d,\"image_frames\":%d,\"reset_count\":1,"
                "\"motion_ticks\":%llu,\"smooth_presentation\":true,\"mirror\":false,"
                "\"motion_overflows\":%u,\"renderer_overflows\":%u,\"segments\":[",
                fps, total, static_cast<unsigned long long>(motion->frame()),
                motion->overflowCount(), renderer->overflowCount());
    for (size_t i = 0; i < segments.size(); ++i) {
        const auto& item = segments[i];
        std::printf("%s{\"state\":\"%s\",\"start_frame\":%d,\"frames\":%d}",
                    i ? "," : "", item.state.c_str(), item.start, item.frames);
    }
    std::printf("]}\n");
    return 0;
}
