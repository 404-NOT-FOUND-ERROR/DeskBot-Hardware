#include "head_touch_wifi.h"
#include <cassert>
#include <cstring>
#include <iostream>

using namespace deskbot_head_touch_wifi;
int main() {
    char device[33], boot[33], firmware[65], output[288];
    std::memset(device, 'a', 32); device[32] = 0;
    std::memset(boot, 'b', 32); boot[32] = 0;
    std::memset(firmware, 'c', 64); firmware[64] = 0;
    assert(Encode(output, sizeof(output), device, boot, firmware, 9007199254740000ULL, 9007199254740991ULL));
    assert(std::strstr(output, "\"uptime_ms\":9007199254740000"));
    assert(std::strstr(output, "\"reported_uptime_ms\":9007199254740991"));
    assert(!Encode(output, 30, device, boot, firmware, 0, 1));
    assert(!Encode(output, sizeof(output), "bad", boot, firmware, 0, 1));
    assert(!Encode(output, sizeof(output), device, boot, firmware, 1, 0));
    assert(Fresh(0, 299999) && !Fresh(0, 300000));
    assert(!Fresh(100, 99));
    assert(RetryDelayMs(0) == 1000 && RetryDelayMs(4) == 16000 && RetryDelayMs(99) == 30000);
    assert(Classify(204) == Delivery::Recorded);
    assert(Classify(200) == Delivery::Retry && Classify(302) == Delivery::Retry);
    assert(Classify(400) == Delivery::Rejected && Classify(409) == Delivery::Rejected);
    assert(Classify(0) == Delivery::Retry && Classify(503) == Delivery::Retry);
    std::cout << "head-touch-wifi core passed\n";
}
