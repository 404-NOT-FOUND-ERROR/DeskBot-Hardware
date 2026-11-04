#include "studio_device_link.h"
#include <cassert>
#include <iostream>
#include <string>

int main() {
    using deskbot_studio_link::BuildHello;
    const std::string device(32, 'a'), boot(32, 'b'), hash(64, 'c');
    char decimal[21];
    deskbot_studio_link::Decimal64(0, decimal);
    assert(std::string(decimal) == "0");
    deskbot_studio_link::Decimal64(UINT64_MAX, decimal);
    assert(std::string(decimal) == "18446744073709551615");
    char result[1024];
    assert(BuildHello(result, sizeof(result), device.c_str(), boot.c_str(), hash.c_str(),
                      "2.0.5", 0, 1000, 33554432, 16777216));
    std::cout << result << '\n';
    assert(BuildHello(result, sizeof(result), device.c_str(), boot.c_str(), hash.c_str(),
                      "2.0.5", UINT32_MAX, 9007199254740991ULL, UINT32_MAX, UINT32_MAX));
    std::cout << result << '\n';
    assert(!BuildHello(result, 10, device.c_str(), boot.c_str(), hash.c_str(), "2.0.5", 0, 1, 0, 0));
    assert(result[0] == '\0');
    assert(!BuildHello(result, sizeof(result), "wrong", boot.c_str(), hash.c_str(), "2.0.5", 0, 1, 0, 0));
    assert(!BuildHello(result, sizeof(result), device.c_str(), boot.c_str(), hash.c_str(), "bad\"version", 0, 1, 0, 0));
    assert(!BuildHello(nullptr, 0, device.c_str(), boot.c_str(), hash.c_str(), "2.0.5", 0, 1, 0, 0));
    assert(!BuildHello(result, sizeof(result), device.c_str(), boot.c_str(), hash.c_str(), "", 0, 1, 0, 0));
    assert(!BuildHello(result, sizeof(result), device.c_str(), boot.c_str(), hash.c_str(), "2.0.5", 0, 9007199254740992ULL, 0, 0));
}
