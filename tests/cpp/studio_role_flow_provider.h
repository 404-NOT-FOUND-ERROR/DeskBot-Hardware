#pragma once

#include "studio_catalog.h"
#include <fstream>
#include <string>
#include <vector>

// Test transport for rows built from the installed package by the production
// catalog builder. Session IDs, clocks and sequences belong to the endpoint.
namespace role_flow {
static std::string catalog, package, index;
static std::vector<std::string> entries;

inline bool Load(const char* path) {
    std::ifstream file(path);
    if (!std::getline(file, catalog) || !std::getline(file, package) ||
        !std::getline(file, index) || catalog.size() != 64 ||
        package.size() != 64 || index.size() != 64) return false;
    std::string line;
    while (std::getline(file, line)) {
        if (line.empty() || line.size() >= deskbot_studio_catalog::kEntryBytes ||
            entries.size() >= deskbot_studio_catalog::kMaxEntries) return false;
        entries.push_back(line);
    }
    return !entries.empty();
}

inline bool Read(uint16_t cursor, deskbot_studio_catalog::Page& page) {
    namespace cat = deskbot_studio_catalog;
    if (cursor >= entries.size() || cursor % cat::kPageSize) return false;
    std::strcpy(page.catalog, catalog.c_str());
    std::strcpy(page.package, package.c_str());
    std::strcpy(page.index, index.c_str());
    page.count = static_cast<uint16_t>(entries.size());
    page.cursor = cursor;
    for (size_t i = 0; i < cat::kPageSize && cursor + i < entries.size(); ++i)
        std::strcpy(page.rows[i], entries[cursor + i].c_str());
    return true;
}
}  // namespace role_flow
