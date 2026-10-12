#ifndef DESKBOT_HOST_SETTINGS_H
#define DESKBOT_HOST_SETTINGS_H

#include <string>

class Settings {
public:
    Settings(const std::string&, bool read_write = false)
        : read_write_(read_write) {}

    std::string GetString(const std::string&, const std::string& fallback = "")
    {
        return value_.empty() ? fallback : value_;
    }

    void SetString(const std::string&, const std::string& value)
    {
        if (read_write_) value_ = value;
    }

    static void Reset() { value_.clear(); }
    static const std::string& Stored() { return value_; }

private:
    bool read_write_;
    inline static std::string value_;
};

#endif
