#pragma once
#include <cstdint>

namespace deskbot_body_wifi {
enum class Channel { None, Touch, Expression };

class Schedule {
public:
    void Reconnect() { touch_due_ = expression_due_ = 0; }
    Channel Next(uint64_t now) const {
        const bool touch = now >= touch_due_, expression = now >= expression_due_;
        if (touch && expression) return last_ == Channel::Touch ? Channel::Expression : Channel::Touch;
        return touch ? Channel::Touch : expression ? Channel::Expression : Channel::None;
    }
    void CompleteTouch(uint64_t ended, uint32_t delay) {
        touch_due_ = ended + delay;
        last_ = Channel::Touch;
    }
    void CompleteExpression(uint64_t ended, bool success) {
        constexpr uint32_t delays[] = {1500, 3000, 6000, 12000, 30000};
        if (success) expression_attempt_ = 0;
        else if (expression_attempt_ < 4) ++expression_attempt_;
        expression_due_ = ended + delays[expression_attempt_];
        last_ = Channel::Expression;
    }
    uint32_t SleepMs(uint64_t now) const {
        const auto due = touch_due_ < expression_due_ ? touch_due_ : expression_due_;
        // Yield even with a backlog. The other channel gets the next due turn.
        if (due <= now || due - now < 10) return 10;
        return due - now > 250 ? 250 : static_cast<uint32_t>(due - now);
    }
private:
    uint64_t touch_due_ = 0, expression_due_ = 0;
    unsigned expression_attempt_ = 0;
    Channel last_ = Channel::Expression;
};

#if __has_include("head_touch_wifi.local.h") && __has_include("wifi_expression.local.h")
void Start(const char* device, const char* boot, const char* firmware);
#else
inline void Start(const char*, const char*, const char*) {}
#endif
}  // namespace deskbot_body_wifi
