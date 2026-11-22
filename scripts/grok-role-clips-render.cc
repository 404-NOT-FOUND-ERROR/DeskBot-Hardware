// Offline scene composition; the pinned upstream sources remain unchanged.
#include "BotRenderer.h"
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr int kSize = 360, kFPS = 20, kEdge = 11, kLoop = 49;
constexpr unsigned kSeed = 20261111;
bool allowed(const std::string& name) {
    for (const char* state : {"idle", "happy", "sad", "shy", "confused", "drowsy"})
        if (name == state) return true;
    return false;
}
float ease5(float t) {
    t = bot::clamp(t);
    return t * t * t * (10 + t * (-15 + 6 * t));
}
bot::Matrix blendMatrix(const bot::Matrix& a, const bot::Matrix& b, float t) {
    if (t <= 0) return a;
    if (t >= 1) return b;
    // Upstream pose matrices have positive scale and no shear. Blend decomposed
    // rotation/scale about HC: coefficient interpolation would shrink a rotation.
    auto scale = [](const bot::Matrix& m) {
        const float x = std::hypot(m.a, m.b), determinant = m.a * m.d - m.b * m.c;
        if (x <= 0 || determinant <= 0 || std::abs(m.a * m.c + m.b * m.d) > .001f)
            throw std::runtime_error("unsupported mirrored, singular or sheared pose");
        return bot::Vec2{x, determinant / x};
    };
    const auto sa = scale(a), sb = scale(b);
    const float aa = std::atan2(a.b, a.a), ab = std::atan2(b.b, b.a);
    const float angle = aa + std::remainder(ab - aa, 2 * bot::kPi) * t;
    const auto ca = a.apply({bot::HC, bot::HC}), cb = b.apply({bot::HC, bot::HC});
    return bot::Matrix::translate(bot::mix(ca.x, cb.x, t), bot::mix(ca.y, cb.y, t)) *
        bot::Matrix::rotate(angle * 180 / bot::kPi) *
        bot::Matrix::scale(bot::mix(sa.x, sb.x, t), bot::mix(sa.y, sb.y, t)) *
        bot::Matrix::translate(-bot::HC, -bot::HC);
}
void blendEye(bot::Vec2* target, const bot::Vec2* a, const bot::Vec2* b, float t) {
    if (t <= 0 || std::memcmp(a, b, 48 * sizeof(bot::Vec2)) == 0) {
        std::memcpy(target, a, 48 * sizeof(bot::Vec2));
        return;
    }
    if (t >= 1) { std::memcpy(target, b, 48 * sizeof(bot::Vec2)); return; }
    bot::lerpEye(target, a, b, t);
    bot::Vec2 canonical[2][48];
    for (int end = 0; end < 2; ++end) {
        auto axes = bot::eyeAxes(end ? b : a);
        const float straight = std::max(0.0f, axes.halfLen - axes.halfWidth);
        axes.halfWidth = std::max(.35f, axes.halfWidth);
        axes.halfLen = straight + axes.halfWidth;
        bot::capsule(canonical[end], axes);
    }
    // The upstream interpolator re-fits a capsule for 0<t<1 but copies original
    // points at the ends. Preserve each endpoint's fitting residual so the
    // geometry tends continuously to the actual original contour at both ends.
    for (int i = 0; i < 48; ++i)
        target[i] = target[i] + (a[i] - canonical[0][i]) * (1 - t) +
                    (b[i] - canonical[1][i]) * t;
}
bot::Scene blendScene(const bot::Scene& a, const bot::Scene& b, float t) {
    if (t <= 0) return a;
    if (t >= 1) return b;
    if (a.head.kind != bot::StaticPath || b.head.kind != bot::StaticPath ||
        a.head.commands != b.head.commands || a.head.commandCount != b.head.commandCount)
        throw std::runtime_error("a common fixed head path is required");
    bot::Scene result = a;
    result.headTransform = blendMatrix(a.headTransform, b.headTransform, t);
    result.headOpacity = bot::mix(a.headOpacity, b.headOpacity, t);
    result.presentationScale = bot::mix(a.presentationScale, b.presentationScale, t);
    for (int eye = 0; eye < 2; ++eye) {
        const auto &left = a.eyes[eye], &right = b.eyes[eye];
        auto& target = result.eyes[eye];
        if (left.kind != bot::SmoothRing || right.kind != bot::SmoothRing ||
            left.count != 48 || right.count != 48)
            throw std::runtime_error("the original 48-point eye topology is required");
        blendEye(target.points, left.points, right.points, t);
        target.transform = blendMatrix(left.transform, right.transform, t);
        target.opacity = bot::mix(left.opacity, right.opacity, t);
    }
    return result;
}
std::vector<bot::Scene> sample(const std::string& state) {
    auto motion = std::make_unique<bot::Motion>();
    motion->reset(kSeed);
    if (!motion->setShape("blob") || !motion->setState(state.c_str()))
        throw std::runtime_error("invalid circular state");
    motion->setSmoothPresentation(true); // reset() clears this flag.
    motion->setDisplaySize(256);
    for (int i = 0; i < 36; ++i) motion->tick();
    std::vector<bot::Scene> scenes;
    for (int i = 0; i < kLoop; ++i) {
        if (i) for (int tick = 0; tick < 2; ++tick) motion->tick();
        if (i == 24) motion->blink();
        scenes.push_back(motion->scene());
        // Separate action clips will retain particles and full turns. This loop
        // samples 0.6..2.2s, before the earliest automatic play action at 2.5s.
        for (auto& extra : scenes.back().extras) extra = bot::DrawItem{};
    }
    if (motion->overflowCount() || motion->shape() != 0 || motion->frame() != 132)
        throw std::runtime_error("native source clock or shape differs");
    return scenes;
}
bool write32(FILE* file, unsigned value) {
    const unsigned char bytes[] = {static_cast<unsigned char>(value),
        static_cast<unsigned char>(value >> 8), static_cast<unsigned char>(value >> 16),
        static_cast<unsigned char>(value >> 24)};
    return std::fwrite(bytes, 1, 4, file) == 4;
}
bool writeClip(const std::string& path, const std::vector<bot::Scene>& scenes,
               bot::Motion& carrier, bot::Renderer& renderer, const bot::ViewConfig& view) {
    FILE* file = std::fopen(path.c_str(), "wb");
    if (!file) return false;
    std::vector<unsigned char> gray(kSize * kSize), alpha(kSize * kSize);
    bool valid = std::fwrite("GKR2", 1, 4, file) == 4 && write32(file, kSize) &&
        write32(file, kSize) && write32(file, kFPS) && write32(file, scenes.size());
    for (const auto& scene : scenes) {
        if (!valid) break;
        // carrier is non-const: modifying its public const-view is defined.
        // It is used only as a render carrier and is never ticked afterwards.
        const_cast<bot::Scene&>(carrier.scene()) = scene;
        valid = renderer.renderOverlay(carrier, view, gray.data(), alpha.data()) &&
            !renderer.overflowCount() &&
            std::fwrite(gray.data(), 1, gray.size(), file) == gray.size() &&
            std::fwrite(alpha.data(), 1, alpha.size(), file) == alpha.size();
    }
    if (std::fclose(file)) valid = false;
    if (!valid) std::remove(path.c_str());
    return valid;
}
bool selfTest() {
    auto a = bot::Matrix::translate(bot::HC, bot::HC) * bot::Matrix::rotate(170) *
        bot::Matrix::scale(2, 3) * bot::Matrix::translate(-bot::HC, -bot::HC);
    auto b = bot::Matrix::translate(bot::HC + 10, bot::HC - 6) * bot::Matrix::rotate(-170) *
        bot::Matrix::scale(2, 3) * bot::Matrix::translate(-bot::HC, -bot::HC);
    const auto mid = blendMatrix(a, b, .5f);
    const auto center = mid.apply({bot::HC, bot::HC});
    if (std::abs(mid.a + 2) > .0001f || std::abs(mid.d + 3) > .0001f ||
        std::abs(center.x - bot::HC - 5) > .001f || std::abs(center.y - bot::HC + 3) > .001f)
        return false;
    const auto exact = blendMatrix(a, b, 0);
    if (exact.a != a.a || exact.x != a.x || ease5(0) != 0 || ease5(1) != 1) return false;
    auto mirrored = b;
    mirrored.a = -mirrored.a;
    mirrored.b = -mirrored.b;
    try { blendMatrix(a, mirrored, .5f); return false; } catch (const std::runtime_error&) {}
    bot::Vec2 first[48], second[48], blended[48];
    std::memcpy(first, bot::eyePoses[0][0], sizeof(first));
    std::memcpy(second, bot::eyePoses[3][0], sizeof(second));
    blendEye(blended, first, first, .5f);
    if (std::memcmp(blended, first, sizeof(first))) return false;
    blendEye(blended, first, second, .000001f);
    for (int i = 0; i < 48; ++i)
        if (bot::length(blended[i] - first[i]) > .001f) return false;
    blendEye(blended, first, second, .999999f);
    for (int i = 0; i < 48; ++i)
        if (bot::length(blended[i] - second[i]) > .001f) return false;
    return true;
}
}

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--self-test") {
        if (!selfTest()) return 6;
        std::puts("{\"geometry_tests\":\"passed\"}");
        return 0;
    }
    if (argc != 3 || !allowed(argv[1])) return 2;
    std::vector<std::string> paths;
    for (const char* phase : {"entry", "loop", "exit"})
        paths.push_back(std::string(argv[2]) + "/" + phase + ".gray");
    try {
        const auto neutral = sample("idle");
        const auto original = sample(argv[1]);
        const auto &home = neutral[12], &pose = original[12];
        std::vector<bot::Scene> entry, loop, exit;
        for (int frame = 0; frame < kEdge; ++frame) {
            const float t = ease5(frame / float(kEdge - 1));
            entry.push_back(blendScene(home, pose, t));
            exit.push_back(blendScene(pose, home, t));
        }
        for (int frame = 0; frame < kLoop; ++frame) {
            // Ease native short motion in/out of P; exact P at both ends.
            const float envelope = ease5(frame / 10.0f) * ease5((kLoop - 1 - frame) / 10.0f);
            loop.push_back(blendScene(pose, original[frame], envelope));
        }
        auto renderer = std::make_unique<bot::Renderer>();
        auto carrier = std::make_unique<bot::Motion>();
        std::vector<unsigned char> workspace(bot::Renderer::WORK_BYTES);
        if (!renderer->begin(workspace.data()) || !renderer->setSize(kSize, kSize)) return 3;
        bot::ViewConfig view;
        view.width = view.height = kSize;
        view.botSize = 256;
        view.solid = true;
        view.mirrorX = false;
        view.particles = false;
        if (!writeClip(paths[0], entry, *carrier, *renderer, view) ||
            !writeClip(paths[1], loop, *carrier, *renderer, view) ||
            !writeClip(paths[2], exit, *carrier, *renderer, view))
            throw std::runtime_error("clip rasterization failed");
        std::printf("{\"state\":\"%s\",\"shape\":\"blob\",\"fps\":20,\"entry_frames\":11,"
                    "\"loop_frames\":49,\"exit_frames\":11,\"mirror\":false,\"particles\":false,"
                    "\"native_ticks_per_state\":132,\"native_anchor_tick\":60,"
                    "\"native_first_tick\":36,\"native_last_tick\":132,"
                    "\"renderer_overflows\":%u}\n", argv[1], renderer->overflowCount());
        return 0;
    } catch (const std::exception& error) {
        for (const auto& path : paths) std::remove(path.c_str());
        std::fprintf(stderr, "%s\n", error.what());
        return 5;
    }
}
