#pragma once
#include <cstddef>
#include <cstdint>

// Pure decoder/compositor, also compiled by the native pixel/state tests.
// Its caller owns serialization, mapped resource lifetime and PSRAM allocation.
namespace deskbot_face {
constexpr int kSide = 360;
constexpr size_t kSnapshotBytes = 2 * kSide * kSide * 8;
enum class Phase : uint8_t { Idle, Entry, Body, Exit };
struct Rect { float x=0,y=0,r=0,b=0; };
struct Region { int16_t x=0,y=0,w=0,h=0; };
struct Source {
    Rect rect{};
    uint16_t sprite=65535;
    uint8_t flip=0, bank=255;
    bool Present() const { return sprite != 65535 || bank != 255; }
};
struct Blend { Source a{},b{}; Rect rect{}; float u=0; };
struct Pose { Blend eyes[4]{}; Source accent[2]{}; float accent_u=0; uint8_t color[3]{}; };
struct Clip { uint32_t first=0,count=0; uint16_t body=0; bool loop=false,mirror=false; uint8_t color[3]{}; };
struct Sprite { uint16_t w=0,h=0; uint8_t channels=0; uint32_t rows=0,data=0,bytes=0; };
struct MaskSpan { uint16_t first=0,end=0,solid_first=0,solid_end=0; };
class Pack {
public:
    bool Open(const uint8_t* data,size_t bytes);
    int Find(const char* semantic) const;
    Clip GetClip(unsigned index) const;
    Sprite GetSprite(unsigned index) const;
    Source Ref(unsigned frame,unsigned layer) const;
    bool DecodeRow(unsigned sprite,unsigned y,uint8_t* out,MaskSpan* span=nullptr) const;
    unsigned Count() const { return clips_; }
    unsigned Fps() const { return fps_; }
    unsigned TransitionMs() const { return transition_; }
    bool ClipPupils() const { return flags_ & 1; }
    bool ExitFromLast() const { return flags_ & 2; }
private:
    const uint8_t* data_=nullptr;
    size_t bytes_=0;
    uint32_t sprites_=0,sprite_at_=0,clips_=0,clip_at_=0,keys_=0,key_at_=0;
    unsigned fps_=0,transition_=0,flags_=0;
};
class Player {
public:
    // Workspace has exactly kSnapshotBytes. No allocation occurs in any frame.
    bool Init(const Pack& pack,uint8_t* workspace,size_t bytes);
    bool Request(const char* semantic,bool replay=false);
    void Cancel();
    void Advance(uint32_t elapsed_ms);
    bool Draw(int x,int y,int width,int height,uint16_t* out,int stride,bool swap=false);
    // Up to three local rectangles cover every changed pixel, including the
    // previous positions. A changed background requires one full-screen area.
    unsigned Damage(const Pose& previous,Region* regions) const;
    Phase GetPhase() const { return phase_; }
    unsigned Current() const { return current_; }
    unsigned BodyCycles() const { return body_cycles_; }
    uint32_t Clock() const { return clock_; }
    const Pose& CurrentPose() const { return pose_; }
private:
    struct Axis { uint16_t a=0,b=0,w=0; };
    struct Plan {
        Source source{}; Sprite sprite{}; Rect rect{};
        Axis x[kSide]{};
        int first=0,end=0;
    } plans_[10]{};
    struct Scan { const uint8_t* a=nullptr;const uint8_t* b=nullptr;unsigned w=0;MaskSpan span{}; };
    uint32_t line_[kSide]{};
    bool compiled_=false;
    struct RowCache {
        uint16_t sprite=65535;
        int row[2]{-1,-1};
        MaskSpan span[2]{};
    } rows_[10]{};
    MaskSpan snapshot_spans_[2][4][kSide]{};
    uint8_t mask_rows_[8][2][kSide]{};
    uint8_t accent_rows_[2][2][kSide*4]{};
    uint8_t* CachedRow(unsigned cache,unsigned row) {
        return cache<8?mask_rows_[cache][row]:accent_rows_[cache-8][row];
    }
    const Pack* pack_=nullptr;
    uint8_t* snapshots_=nullptr;
    Source frozen_[5]{};
    Pose pose_{};
    uint8_t from_color_[3]{};
    Phase phase_=Phase::Idle;
    unsigned current_=0,idle_=0,wanted_=0,body_cycles_=0;
    uint32_t clock_=0;
    uint8_t bank_=0;
    void Evaluate();
    Source OrientedRef(const Clip& clip,unsigned frame,unsigned layer) const;
    void BodyPose(float progress);
    void TransitionPose(float progress);
    void Freeze();
    void Begin(unsigned target,Phase phase);
    void Sample(const Source& source,unsigned layer,float u,float v,uint8_t* rgba,unsigned cache);
    void Mask(const Blend& blend,unsigned layer,float x,float y,uint8_t* rgba);
    void Accent(const Pose& pose,float x,float y,uint8_t* rgba);
    void Compile();
    Scan ScanRow(unsigned cache,float y);
    MaskSpan MaskBounds(unsigned layer,const Scan& a,const Scan& b) const;
    uint8_t Value(unsigned cache,const Scan& scan,int x,unsigned channel=0) const;
    uint8_t MaskValue(unsigned layer,int x,const Scan& a,const Scan& b) const;
    void AccentValue(int x,const Scan& a,const Scan& b,uint8_t* rgba) const;
};
}  // namespace deskbot_face
