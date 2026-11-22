#pragma once
#include <esp_heap_caps.h>
#include <cmath>
#include <climits>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <initializer_list>
#include <new>

// Role files are parsed in a private PSRAM arena. Small cJSON allocations use
// internal RAM and can otherwise compete with Wi-Fi initialization. This parser
// leaves process-wide JSON allocator hooks and other tasks unchanged.
namespace deskbot_role_json {
enum class Kind { Object, Array, String, Number, True, False, Null };
struct Node {
    Node *next = nullptr, *child = nullptr;
    char *string = nullptr, *valuestring = nullptr;
    double valuedouble = 0;
    int valueint = 0;
    Kind kind = Kind::Null;
};
inline bool IsObject(const Node* n) { return n && n->kind == Kind::Object; }
inline bool IsArray(const Node* n) { return n && n->kind == Kind::Array; }
inline bool IsString(const Node* n) { return n && n->kind == Kind::String; }
inline bool IsNumber(const Node* n) { return n && n->kind == Kind::Number; }
inline bool IsTrue(const Node* n) { return n && n->kind == Kind::True; }
inline bool IsBool(const Node* n) { return n && (n->kind == Kind::True || n->kind == Kind::False); }
inline const Node* GetObjectItemCaseSensitive(const Node* n, const char* name) {
    if (!IsObject(n)) return nullptr;
    for (n = n->child; n; n = n->next) if (n->string && !std::strcmp(n->string, name)) return n;
    return nullptr;
}
inline int GetArraySize(const Node* n) {
    int size = 0;
    if (IsArray(n)) for (n = n->child; n; n = n->next) ++size;
    return size;
}
inline const Node* GetArrayItem(const Node* n, int index) {
    if (!IsArray(n) || index < 0) return nullptr;
    n = n->child; while (n && index--) n = n->next;
    return n;
}
class Document {
public:
    Document() = default;
    Document(const Document&) = delete;
    Document& operator=(const Document&) = delete;
    ~Document() { heap_caps_free(text_); heap_caps_free(nodes_); }
    const Node* Parse(const uint8_t* raw, size_t bytes) {
        if (text_ || nodes_ || !raw || !bytes || bytes > 65536) return nullptr;
        // Every possible node has a comma/colon, literal or two quote bytes.
        // Count structural starts outside strings to bound the single arena.
        bool quoted = false;
        capacity_ = 1;
        for (size_t i = 0; i < bytes; ++i) {
            if (quoted && raw[i] == '\\') { if (++i == bytes) return nullptr; continue; }
            if (raw[i] == '"') quoted = !quoted;
            if (!quoted && (raw[i] == ',' || raw[i] == ':' || raw[i] == '[' || raw[i] == '{')) ++capacity_;
        }
        text_ = static_cast<char*>(heap_caps_malloc(bytes + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        nodes_ = static_cast<Node*>(heap_caps_malloc(capacity_ * sizeof(Node), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (!text_ || !nodes_) return nullptr;
        std::memcpy(text_, raw, bytes); text_[bytes] = 0; at_ = text_; limit_ = text_ + bytes;
        Node* root = Value(0); Space();
        return root && at_ == limit_ ? root : nullptr;
    }
private:
    void Space() { while (at_ < limit_ && (*at_ == ' ' || *at_ == '\n' || *at_ == '\r' || *at_ == '\t')) ++at_; }
    char* String() {
        if (at_ == limit_ || *at_++ != '"') return nullptr;
        char *start = at_, *out = start;
        while (at_ < limit_) {
            const auto c = static_cast<unsigned char>(*at_++);
            if (c == '"') { *out = 0; return start; }
            if (c < 32) return nullptr;
            if (c != '\\') { *out++ = char(c); continue; }
            if (at_ == limit_) return nullptr;
            const char escape = *at_++;
            if (escape == '"' || escape == '\\' || escape == '/') *out++ = escape;
            else if (escape == 'b') *out++ = '\b';
            else if (escape == 'f') *out++ = '\f';
            else if (escape == 'n') *out++ = '\n';
            else if (escape == 'r') *out++ = '\r';
            else if (escape == 't') *out++ = '\t';
            else if (escape == 'u') {
                uint32_t code = 0;
                if (!Hex4(code) || !code) return nullptr;
                if (code >= 0xD800 && code <= 0xDBFF) {
                    if (limit_ - at_ < 6 || at_[0] != '\\' || at_[1] != 'u') return nullptr;
                    at_ += 2; uint32_t low = 0;
                    if (!Hex4(low) || low < 0xDC00 || low > 0xDFFF) return nullptr;
                    code = 0x10000 + ((code - 0xD800) << 10) + low - 0xDC00;
                } else if (code >= 0xDC00 && code <= 0xDFFF) return nullptr;
                if (code < 0x80) *out++ = char(code);
                else if (code < 0x800) { *out++ = char(0xC0 | (code >> 6)); *out++ = char(0x80 | (code & 63)); }
                else {
                    if (code >= 0x10000) { *out++ = char(0xF0 | (code >> 18)); *out++ = char(0x80 | ((code >> 12) & 63)); }
                    else *out++ = char(0xE0 | (code >> 12));
                    *out++ = char(0x80 | ((code >> 6) & 63)); *out++ = char(0x80 | (code & 63));
                }
            } else return nullptr;
        }
        return nullptr;
    }
    bool Hex4(uint32_t& value) {
        if (limit_ - at_ < 4) return false;
        for (unsigned i = 0; i < 4; ++i) {
            const char c = *at_++;
            const int digit = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
            if (digit < 0) return false;
            value = (value << 4) | unsigned(digit);
        }
        return true;
    }
    Node* Value(unsigned depth) {
        Space(); if (at_ == limit_ || depth > 8 || used_ == capacity_) return nullptr;
        Node* node = new (&nodes_[used_++]) Node{};
        if (*at_ == '{' || *at_ == '[') {
            const bool object = *at_++ == '{'; const char close = object ? '}' : ']';
            node->kind = object ? Kind::Object : Kind::Array;
            Node** tail = &node->child;
            Space(); if (at_ < limit_ && *at_ == close) { ++at_; return node; }
            while (at_ < limit_) {
                char* key = nullptr;
                if (object) {
                    Space(); key = String(); if (!key) return nullptr;
                    Space(); if (at_ == limit_ || *at_++ != ':') return nullptr;
                }
                Node* child = Value(depth + 1); if (!child) return nullptr;
                child->string = key; *tail = child; tail = &child->next;
                Space(); if (at_ == limit_) return nullptr;
                const char separator = *at_++;
                if (separator == close) return node;
                if (separator != ',') return nullptr;
                Space(); if (at_ == limit_ || *at_ == close) return nullptr;
            }
            return nullptr;
        }
        if (*at_ == '"') { node->kind = Kind::String; node->valuestring = String(); return node->valuestring ? node : nullptr; }
        for (const auto& literal : {Literal{"true", Kind::True}, Literal{"false", Kind::False}, Literal{"null", Kind::Null}}) {
            const size_t size = std::strlen(literal.text);
            if (size_t(limit_ - at_) >= size && !std::memcmp(at_, literal.text, size)) {
                at_ += size; node->kind = literal.kind; return node;
            }
        }
        char* start = at_;
        if (*at_ == '-') ++at_;
        if (at_ == limit_ || *at_ < '0' || *at_ > '9') return nullptr;
        if (*at_ == '0') ++at_;
        else while (at_ < limit_ && *at_ >= '0' && *at_ <= '9') ++at_;
        if (at_ < limit_ && *at_ == '.') {
            ++at_; char* digits = at_;
            while (at_ < limit_ && *at_ >= '0' && *at_ <= '9') ++at_;
            if (at_ == digits) return nullptr;
        }
        if (at_ < limit_ && (*at_ == 'e' || *at_ == 'E')) {
            ++at_; if (at_ < limit_ && (*at_ == '-' || *at_ == '+')) ++at_;
            char* digits = at_;
            while (at_ < limit_ && *at_ >= '0' && *at_ <= '9') ++at_;
            if (at_ == digits) return nullptr;
        }
        char* end = nullptr; node->valuedouble = std::strtod(start, &end);
        if (end != at_ || !std::isfinite(node->valuedouble)) return nullptr;
        node->valueint = node->valuedouble >= INT_MAX ? INT_MAX : node->valuedouble <= INT_MIN ? INT_MIN : static_cast<int>(node->valuedouble);
        node->kind = Kind::Number; return node;
    }
    struct Literal { const char* text; Kind kind; };
    char *text_ = nullptr, *at_ = nullptr, *limit_ = nullptr;
    Node* nodes_ = nullptr;
    size_t used_ = 0, capacity_ = 0;
};
}  // namespace deskbot_role_json
