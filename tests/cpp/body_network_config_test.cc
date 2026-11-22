#include "body_network_config.inc"
#include "body_network_nvs_stub.h"
#include "studio_usb_session.h"
#include <cassert>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    using namespace deskbot_body_network;
    assert(Initialize("10.0.0.1", 4318, 4319));
    if (argc > 1) {
        if (argc > 2 && !std::strcmp(argv[2], "set-uncertain"))
            network_fixture::fail_set = network_fixture::persist_set_error = true;
        deskbot_studio_session::Endpoint endpoint;
        endpoint.Initialize(std::string(32, 'a').c_str(), std::string(32, 'b').c_str());
        endpoint.SetNetworkProvider(Handle);
        char output[1024]; std::string line;
        while (std::getline(std::cin, line)) {
            const auto split = line.find('|');
            const uint64_t now = std::stoull(line.substr(0, split));
            ApplyUrls();  // Simulate the independent worker between USB commands.
            line = line.substr(split + 1) + '\n';
            for (const auto c : line) if (endpoint.Feed(c, now, output, sizeof(output))) std::cout << output << '\n' << std::flush;
        }
        return 0;
    }
    assert(Host("10.0.0.1") && Host("deskbot.example") && !Host("127.0.0.1") && !Host("192.168.01.1"));
    assert(!Host("https://host") && !Host("host..example") && !Host("-host.example") && !Host("host-.example"));
    Command c{};
    assert(Decode("net-set:0:deskbot.example:1:65535", c) && c.config.expression_port == 65535);
    for (const char* op : {"net-set:0:10.0.0.1:0:2", "net-set:00:10.0.0.1:1:2", "net-set:0:10.0.0.1:1:1",
         "net-set:0:10.0.0.1:65536:1", "net-set:4294967296:10.0.0.1:1:2", "net-set:0:10.0.0.1:1:2:3"}) assert(!Decode(op, c));
    Reply r{}; assert(Handle("net-get", r) && r.config.revision == 0 && !r.applied);
    assert(ApplyUrls()); assert(!ApplyUrls());
    assert(!std::strcmp(TouchUrl(), "https://10.0.0.1:4318/api/hardware/head-touch"));
    assert(Handle("net-set:0:10.0.0.2:5318:5319", r) && !std::strcmp(r.result, "saved") && r.config.revision == 1);
    assert(r.applied_revision == 0 && network_fixture::commits == 1);
    assert(std::strstr(TouchUrl(), "10.0.0.1")); // Saving cannot change an in-flight request's pointer.
    assert(Handle("net-set:0:10.0.0.2:5318:5319", r) && !std::strcmp(r.result, "unchanged") && network_fixture::commits == 1);
    assert(Handle("net-set:0:10.0.0.3:5318:5319", r) && !std::strcmp(r.result, "conflict"));
    assert(ApplyUrls() && std::strstr(ExpressionUrl(), "10.0.0.2:5319"));
    assert(Initialize("10.0.0.1", 4318, 4319));
    assert(Handle("net-get", r) && !std::strcmp(r.storage, "restored") && r.config.revision == 1);
    network_fixture::fail_set = true;
    assert(Handle("net-reset:1", r) && !std::strcmp(r.result, "save_uncertain") && r.config.saved);
    const auto failed_writes = network_fixture::writes;
    assert(Handle("net-set:1:10.0.0.3:5318:5319", r) && !std::strcmp(r.result, "save_uncertain"));
    assert(network_fixture::writes == failed_writes); // Resolve storage before accepting another CAS.
    network_fixture::fail_set = false; network_fixture::fail_commit = true;
    assert(Initialize("10.0.0.1", 4318, 4319));
    assert(Handle("net-reset:1", r) && !std::strcmp(r.result, "save_uncertain") && r.config.revision == 1);
    network_fixture::fail_commit = false;
    assert(Initialize("10.0.0.1", 4318, 4319)); assert(Handle("net-get", r) && r.config.revision == 1);
    // A failed set may already be durable (ESP_ERR_NVS_REMOVE_FAILED).
    network_fixture::fail_set = network_fixture::persist_set_error = true;
    assert(Handle("net-set:1:10.0.0.3:5318:5319", r) && !std::strcmp(r.result, "save_uncertain"));
    assert(r.config.revision == 1 && !std::strcmp(r.config.host, "10.0.0.2"));
    network_fixture::fail_set = network_fixture::persist_set_error = false;
    assert(Initialize("10.0.0.1", 4318, 4319));
    assert(Handle("net-get", r) && r.config.revision == 2 && !std::strcmp(r.config.host, "10.0.0.3"));
    assert(Handle("net-reset:1", r) && !std::strcmp(r.result, "conflict"));
    network_fixture::fail_commit = network_fixture::persist_commit_error = true;
    assert(Handle("net-reset:2", r) && !std::strcmp(r.result, "save_uncertain") && r.config.revision == 2);
    network_fixture::fail_commit = network_fixture::persist_commit_error = false;
    assert(Initialize("10.0.0.1", 4318, 4319)); assert(Handle("net-get", r) && r.config.revision == 3 && !r.config.saved);
    assert(Handle("net-set:3:10.0.0.2:5318:5319", r) && !std::strcmp(r.result, "saved") && r.config.revision == 4);
    assert(Handle("net-reset:4", r) && !std::strcmp(r.result, "saved") && !r.config.saved && r.config.revision == 5);
    assert(Initialize("10.0.0.9", 6318, 6319));
    assert(Handle("net-get", r) && !r.config.saved && !std::strcmp(r.config.host, "10.0.0.9") && r.config.revision == 5);
    network_fixture::committed[0] ^= 1;
    assert(Initialize("10.0.0.1", 4318, 4319));
    assert(Handle("net-get", r) && !std::strcmp(r.storage, "invalid_record") && r.config.revision == 0);
    network_fixture::fail_open = true;
    assert(Handle("net-set:0:10.0.0.2:5318:5319", r) && !std::strcmp(r.result, "save_failed"));
    std::cout << "body network settings passed\n";
}
