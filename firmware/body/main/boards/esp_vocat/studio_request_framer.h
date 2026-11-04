#pragma once

#include <cstddef>
#include <cstdint>

namespace deskbot_studio_input {
constexpr std::size_t kMaxFrameBytes = 2048;

enum class FrameKind { None, Line, TooLarge };

struct FrameView {
    FrameKind kind = FrameKind::None;
    const std::uint8_t* data = nullptr;
    std::size_t size = 0;
};

// Byte framing only. The caller validates UTF-8, JSON and request semantics.
// Consume a returned view before the next Feed() or Reset(); no allocation,
// callback, serial driver, UI or task is owned by this object.
class RequestFramer {
public:
    FrameView Feed(std::uint8_t byte) {
        if (byte == '\n') {
            if (overflow_) {
                Reset();
                return {FrameKind::TooLarge, nullptr, 0};
            }
            const auto size = used_;
            used_ = 0;
            bool blank = true;
            for (std::size_t i = 0; i < size; ++i) {
                const auto c = buffer_[i];
                if (!(c == ' ' || c == '\t' || c == '\r' || c == '\v' || c == '\f')) {
                    blank = false;
                    break;
                }
            }
            return blank ? FrameView{} : FrameView{FrameKind::Line, buffer_, size};
        }
        if (!overflow_) {
            if (used_ == kMaxFrameBytes) {
                used_ = 0;
                overflow_ = true;
            } else {
                buffer_[used_++] = byte;
            }
        }
        return {};
    }

    void Reset() {
        used_ = 0;
        overflow_ = false;
    }

    std::size_t BufferedBytes() const { return used_; }

private:
    std::uint8_t buffer_[kMaxFrameBytes]{};
    std::size_t used_ = 0;
    bool overflow_ = false;
};
}  // namespace deskbot_studio_input
