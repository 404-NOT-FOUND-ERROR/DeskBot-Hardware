// DFM1 renderer. Included once by studio_role_player.cc in the layered build.
#include "studio_layered_face.h"
#include <algorithm>
#include <cmath>
#include <cstring>

// Keep the frozen project's size-oriented flags elsewhere. The bounded
// scanline compositor benefits from inlining on the S3's scalar FPU.
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC push_options
#pragma GCC optimize("O3")
#endif
namespace deskbot_face {
namespace {
uint16_t F16(const uint8_t* p) { return uint16_t(p[0]) | uint16_t(p[1])<<8; }
uint32_t F32(const uint8_t* p) { return uint32_t(F16(p)) | uint32_t(F16(p+2))<<16; }
float Float(const uint8_t* p) { const uint32_t n=F32(p); float v; std::memcpy(&v,&n,4); return v; }
float Clamp(float n) { return std::max(0.f,std::min(1.f,n)); }
float Ease(float n) { n=Clamp(n); return n*n*(3-2*n); }
float Mix(float a,float b,float u) { return a+(b-a)*u; }
Rect MixRect(Rect a,Rect b,float u) { return {Mix(a.x,b.x,u),Mix(a.y,b.y,u),Mix(a.r,b.r,u),Mix(a.b,b.b,u)}; }
Blend Between(Source a,Source b,float u) {
    return {a,b,a.Present() && b.Present() ? MixRect(a.rect,b.rect,u) : (a.Present()?a.rect:b.rect),u};
}
void HSV(const uint8_t* c,float* hsv) {
    float r=c[0]/255.f,g=c[1]/255.f,b=c[2]/255.f;
    float mx=std::max(r,std::max(g,b)),mn=std::min(r,std::min(g,b)),d=mx-mn,h=0;
    if (d>0) h=(mx==r?std::fmod((g-b)/d+6,6.f):mx==g?(b-r)/d+2:(r-g)/d+4)/6;
    hsv[0]=h; hsv[1]=mx?d/mx:0; hsv[2]=mx;
}
void Hue(const uint8_t* a,const uint8_t* b,float u,uint8_t* out) {
    if(u<=0) { std::memcpy(out,a,3); return; }
    if(u>=1) { std::memcpy(out,b,3); return; }
    float x[3],y[3]; HSV(a,x); HSV(b,y);
    float h=x[0]+(std::fmod(y[0]-x[0]+1.5f,1.f)-.5f)*u;
    h=std::fmod(h+1,1.f); float s=Mix(x[1],y[1],u),v=Mix(x[2],y[2],u);
    for(int i=0;i<3;++i) { float k=std::fmod((5-2*i)+h*6,6.f);
        out[i]=uint8_t(std::lround(255*v*(1-s*std::max(0.f,std::min(1.f,std::min(k,4-k)))))); }
}
bool Inside(Rect r,float x,float y) { return x>=r.x && x<r.r && y>=r.y && y<r.b; }
void MaskRun(MaskSpan& span,unsigned x,unsigned n,unsigned alpha,unsigned& solid_start,bool& solid) {
    if(alpha) {
        if(!span.end) span.first=uint16_t(x);
        span.end=uint16_t(x+n);
    }
    if(alpha==255) {
        if(!solid) solid_start=x;
        if(x+n-solid_start>unsigned(span.solid_end-span.solid_first)) {
            span.solid_first=uint16_t(solid_start);span.solid_end=uint16_t(x+n);
        }
    }
    solid=alpha==255;
}
}  // namespace
bool Pack::Open(const uint8_t* data,size_t bytes) {
    *this={};
    if(!data || bytes<64 || bytes>8*1024*1024 || std::memcmp(data,"DFM1",4) ||
       F16(data+4)!=1 || F16(data+6)!=kSide || F16(data+8)!=25 || F16(data+10)!=360 || F32(data+12)!=bytes)
        return false;
    const uint32_t ns=F32(data+16),sa=F32(data+20),nc=F32(data+24),ca=F32(data+28),
        nk=F32(data+32),ka=F32(data+36),pa=F32(data+40),pb=F32(data+44);
    if(!ns || ns>2048 || nc!=29 || !nk || nk>2048 || sa!=64 || ca!=sa+ns*24 ||
       ka!=ca+nc*48 || pa!=ka+nk*100 || pa>bytes || pb!=bytes-pa) return false;
    if(F32(data+48)&~3u) return false;
    for(unsigned i=52;i<64;++i) if(data[i]) return false;
    data_=data; bytes_=bytes; sprites_=ns; sprite_at_=sa; clips_=nc; clip_at_=ca; keys_=nk; key_at_=ka;
    fps_=F16(data+8); transition_=F16(data+10); flags_=F32(data+48);
    bool valid=true;
    uint32_t next=pa;
    for(unsigned i=0;valid && i<ns;++i) {
        auto s=GetSprite(i); const auto* row=data+sa+i*24;
        if(!s.w || s.w>kSide || !s.h || s.h>kSide || (s.channels!=1 && s.channels!=4) || row[5]||row[6]||row[7]||
           s.rows!=next || uint64_t(s.rows)+(s.h+1)*4>bytes || s.data!=s.rows+(s.h+1)*4 ||
           s.data>bytes || s.bytes>bytes-s.data || F32(row+20)!=unsigned(s.w)*s.h*s.channels || F32(data+s.rows)!=0 ||
           F32(data+s.rows+s.h*4)!=s.bytes) { valid=false; break; }
        next=s.data+s.bytes;
        for(unsigned y=0;valid && y<s.h;++y) {
            unsigned at=F32(data+s.rows+y*4),end=F32(data+s.rows+(y+1)*4),count=0;
            if(at>=end || end>s.bytes) { valid=false; break; }
            while(valid && at<end) {
                uint8_t token=data[s.data+at++]; unsigned n;
                if(s.channels==1) { n=(token&15)+1;
                    if(n==16) { if(at==end) {valid=false;break;} n+=data[s.data+at++]; }
                } else { n=token;
                    if(!n || end-at<4) { valid=false;break; }
                    const auto* p=data+s.data+at;
                    if(p[0]>p[3] || p[1]>p[3] || p[2]>p[3]) {valid=false;break;}
                    at+=4;
                }
                count+=n; if(count>s.w) valid=false;
            }
            if(count!=s.w) valid=false;
        }
    }
    if(next!=bytes) valid=false;
    for(unsigned i=0;valid && i<nc;++i) {
        const auto* p=data+ca+i*48; auto c=GetClip(i);
        const char* end=static_cast<const char*>(std::memchr(p,0,32));
        if(!end || end==reinterpret_cast<const char*>(p) || c.count<2 || c.count>1500 || c.first>=nk ||
            c.count>nk-c.first || c.body<2 || c.body>1500 || p[42]>1 || p[43]>1 || p[47]) {valid=false;break;}
        for(const auto* q=p;q<reinterpret_cast<const uint8_t*>(end);++q)
            if(!(*q>='a' && *q<='z') && *q!='_') valid=false;
        for(unsigned j=0;j<i;++j) if(!std::strcmp(reinterpret_cast<const char*>(p),reinterpret_cast<const char*>(data+ca+j*48))) valid=false;
    }
    for(unsigned i=0;valid && i<nk;++i) for(unsigned l=0;l<5;++l) {
        const auto* p=data+ka+i*100+l*20; auto r=Ref(i,l);
        if(p[2]>1 || p[3]) {valid=false;break;}
        if(!r.Present()) { for(unsigned j=2;j<20;++j) if(p[j]) valid=false; continue; }
        if(r.sprite>=ns || GetSprite(r.sprite).channels!=(l==4?4:1) ||
           !std::isfinite(r.rect.x)||!std::isfinite(r.rect.y)||!std::isfinite(r.rect.r)||!std::isfinite(r.rect.b)||
           r.rect.x<0||r.rect.y<0||r.rect.r>kSide||r.rect.b>kSide||r.rect.x>=r.rect.r||r.rect.y>=r.rect.b) valid=false;
    }
    if(valid) for(const char* name:{"idle","neutral","happy","winking","confused","crying","eat"}) if(Find(name)<0) valid=false;
    if(!valid) *this={};
    return valid;
}
int Pack::Find(const char* name) const {
    if(!data_ || !name) return -1;
    for(unsigned i=0;i<clips_;++i) if(!std::strcmp(name,reinterpret_cast<const char*>(data_+clip_at_+i*48))) return int(i);
    return -1;
}
Clip Pack::GetClip(unsigned i) const {
    if(i>=clips_) return {};
    const auto* p=data_+clip_at_+i*48;
    return {F32(p+32),F32(p+36),F16(p+40),p[42]!=0,p[43]!=0,{p[44],p[45],p[46]}};
}
Sprite Pack::GetSprite(unsigned i) const {
    if(i>=sprites_) return {};
    const auto* p=data_+sprite_at_+i*24;
    return {F16(p),F16(p+2),p[4],F32(p+8),F32(p+12),F32(p+16)};
}
Source Pack::Ref(unsigned frame,unsigned layer) const {
    if(frame>=keys_ || layer>=5) return {};
    const auto* p=data_+key_at_+frame*100+layer*20;
    return {{Float(p+4),Float(p+8),Float(p+12),Float(p+16)},F16(p),p[2],255};
}
bool Pack::DecodeRow(unsigned sprite,unsigned y,uint8_t* out,MaskSpan* span) const {
    if(sprite>=sprites_ || !out) return false;
    auto s=GetSprite(sprite); if(y>=s.h) return false;
    unsigned at=F32(data_+s.rows+y*4),end=F32(data_+s.rows+(y+1)*4),x=0,solid_start=0;
    bool solid=false;if(span) *span={};
    while(at<end) {
        uint8_t t=data_[s.data+at++]; unsigned n;
        if(s.channels==1) { n=(t&15)+1; if(n==16) n+=data_[s.data+at++];
            std::memset(out+x,uint8_t((t>>4)*17),n);
            if(span) MaskRun(*span,x,n,(t>>4)*17,solid_start,solid);
        } else { n=t; for(unsigned j=0;j<n;++j) std::memcpy(out+(x+j)*4,data_+s.data+at,4); at+=4; }
        x+=n;
    }
    return x==s.w;
}
bool Player::Init(const Pack& pack,uint8_t* workspace,size_t bytes) {
    if(pack.Find("idle")<0 || !workspace || bytes!=kSnapshotBytes) return false;
    pack_=&pack; snapshots_=workspace; idle_=unsigned(pack.Find("idle")); wanted_=current_=idle_;
    phase_=Phase::Idle; clock_=body_cycles_=0; BodyPose(0); return true;
}
void Player::Compile() {
    if(compiled_) return;
    for(unsigned slot=0;slot<10;++slot) {
        auto& plan=plans_[slot];
        plan.source=slot<8?(slot%2?pose_.eyes[slot/2].b:pose_.eyes[slot/2].a):pose_.accent[slot-8];
        plan.rect=slot<8?pose_.eyes[slot/2].rect:plan.source.rect;
        plan.first=plan.end=0;
        if(!plan.source.Present()) continue;
        const float weight=slot<8?pose_.eyes[slot/2].u:pose_.accent_u;
        if((slot%2==0 && weight>=1) || (slot%2==1 && weight<=0)) continue;
        plan.sprite=plan.source.bank==255?pack_->GetSprite(plan.source.sprite):Sprite{
            uint16_t(std::lround(plan.source.rect.r-plan.source.rect.x)),
            uint16_t(std::lround(plan.source.rect.b-plan.source.rect.y)),uint8_t(slot<8?1:4),0,0,0};
        plan.first=std::max(0,int(std::ceil(plan.rect.x-.5f)));
        plan.end=std::min(kSide,int(std::ceil(plan.rect.r-.5f)));
        for(int x=plan.first;x<plan.end;++x) {
            float u=(x+.5f-plan.rect.x)/(plan.rect.r-plan.rect.x);
            float fx=Clamp(u)*plan.sprite.w-.5f;
            if(plan.source.flip) fx=plan.sprite.w-1-fx;
            fx=std::max(0.f,std::min(float(plan.sprite.w-1),fx));
            const unsigned fixed=unsigned(std::lround(fx*256)),left=fixed>>8;
            plan.x[x]={uint16_t(left),uint16_t(std::min(left+1,unsigned(plan.sprite.w)-1)),uint16_t(fixed&255)};
        }
    }
    compiled_=true;
}
Player::Scan Player::ScanRow(unsigned cache,float y) {
    auto& plan=plans_[cache];auto& source=plan.source;
    if(plan.first==plan.end || y<plan.rect.y || y>=plan.rect.b) return {};
    float fy=Clamp((y-plan.rect.y)/(plan.rect.b-plan.rect.y))*plan.sprite.h-.5f;
    fy=std::max(0.f,std::min(float(plan.sprite.h-1),fy));
    const unsigned fixed=unsigned(std::lround(fy*256)),top=fixed>>8;
    const unsigned bottom=(fixed&255)?std::min(top+1,unsigned(plan.sprite.h)-1):top,channels=cache<8?1:4;
    Scan scan{};scan.w=fixed&255;
    MaskSpan spans[2]{};
    if(source.bank!=255) {
        const auto* base=snapshots_+(size_t(source.bank)*8+(cache<8?cache/2:4))*kSide*kSide;
        scan.a=base+top*kSide*channels;scan.b=base+bottom*kSide*channels;
        if(cache<8) {spans[0]=snapshot_spans_[source.bank][cache/2][top];spans[1]=snapshot_spans_[source.bank][cache/2][bottom];}
    } else {
        auto& rows=rows_[cache];
        if(rows.sprite!=source.sprite) {rows.sprite=source.sprite;rows.row[0]=rows.row[1]=-1;}
        const unsigned ys[2]{top,bottom};const uint8_t* pixels[2];
        for(unsigned j=0;j<2;++j) {
            unsigned slot=ys[j]&1;
            auto* target=CachedRow(cache,slot);
            if(rows.row[slot]!=int(ys[j])) {pack_->DecodeRow(source.sprite,ys[j],target,&rows.span[slot]);rows.row[slot]=int(ys[j]);}
            pixels[j]=target;
            spans[j]=rows.span[slot];
        }
        scan.a=pixels[0];scan.b=pixels[1];
    }
    if(cache<8) {
        MaskSpan bounds=spans[0];
        if(scan.w) {
            if(!bounds.end) {bounds.first=spans[1].first;bounds.end=spans[1].end;}
            else if(spans[1].end) {bounds.first=std::min(bounds.first,spans[1].first);bounds.end=std::max(bounds.end,spans[1].end);}
            bounds.solid_first=std::max(bounds.solid_first,spans[1].solid_first);
            bounds.solid_end=std::min(bounds.solid_end,spans[1].solid_end);
        }
        if(source.flip) {
            const auto old=bounds;
            bounds.first=plan.sprite.w-old.end;bounds.end=plan.sprite.w-old.first;
            bounds.solid_first=plan.sprite.w-old.solid_end;bounds.solid_end=plan.sprite.w-old.solid_first;
        }
        const float scale=(plan.rect.r-plan.rect.x)/plan.sprite.w;
        // Expand possible coverage, shrink the known-solid interval. A full
        // source-pixel margin also covers fixed-point coordinate rounding;
        // every unclassified edge still uses the exact original sampler.
        auto bound=[&](float value) {return uint16_t(std::max(plan.first,std::min(plan.end,int(value))));};
        if(bounds.end>bounds.first) {
            scan.span.first=bound(std::floor(plan.rect.x+(int(bounds.first)-1)*scale-1.5f));
            scan.span.end=bound(std::ceil(plan.rect.x+(bounds.end+1)*scale+.5f));
        }
        if(bounds.solid_end>bounds.solid_first+2) {
            scan.span.solid_first=bound(std::ceil(plan.rect.x+(bounds.solid_first+1)*scale+.5f));
            scan.span.solid_end=bound(std::floor(plan.rect.x+(bounds.solid_end-1)*scale-.5f));
        }
    }
    return scan;
}
MaskSpan Player::MaskBounds(unsigned layer,const Scan& a,const Scan& b) const {
    const float u=pose_.eyes[layer].u;
    if(u<=0) return a.span;
    if(u>=1) return b.span;
    MaskSpan out{};
    if(a.span.end>a.span.first) {out.first=a.span.first;out.end=a.span.end;}
    if(b.span.end>b.span.first) {
        out.first=out.end?std::min(out.first,b.span.first):b.span.first;
        out.end=std::max(out.end,b.span.end);
    }
    if(a.span.solid_end>a.span.solid_first && b.span.solid_end>b.span.solid_first) {
        out.solid_first=std::max(a.span.solid_first,b.span.solid_first);
        out.solid_end=std::min(a.span.solid_end,b.span.solid_end);
    }
    return out;
}
uint8_t Player::Value(unsigned cache,const Scan& scan,int x,unsigned channel) const {
    const auto& plan=plans_[cache];
    if(!scan.a || x<plan.first || x>=plan.end) return 0;
    const auto& axis=plan.x[x];const unsigned channels=cache<8?1:4;
    const unsigned aa=scan.a[axis.a*channels+channel];
    if(!axis.w) {
        if(!scan.w) return uint8_t(aa);
        return uint8_t((aa*(256-scan.w)+scan.b[axis.a*channels+channel]*scan.w+128)>>8);
    }
    const unsigned ab=scan.a[axis.b*channels+channel];
    if(!scan.w) return uint8_t((aa*(256-axis.w)+ab*axis.w+128)>>8);
    const unsigned ba=scan.b[axis.a*channels+channel],bb=scan.b[axis.b*channels+channel];
    if(aa==ab && aa==ba && aa==bb) return uint8_t(aa);
    const unsigned a=aa*(256-axis.w)+ab*axis.w;
    const unsigned b=ba*(256-axis.w)+bb*axis.w;
    return uint8_t((a*(256-scan.w)+b*scan.w+32768)>>16);
}
uint8_t Player::MaskValue(unsigned layer,int x,const Scan& a,const Scan& b) const {
    const unsigned va=Value(layer*2,a,x),vb=Value(layer*2+1,b,x);
    if(va==vb) return uint8_t(va);
    return uint8_t(Mix(va,vb,pose_.eyes[layer].u)+.5f);
}
void Player::AccentValue(int x,const Scan& a,const Scan& b,uint8_t* rgba) const {
    for(unsigned c=0;c<4;++c) rgba[c]=uint8_t(Mix(Value(8,a,x,c),Value(9,b,x,c),pose_.accent_u)+.5f);
}
void Player::Sample(const Source& source,unsigned layer,float u,float v,uint8_t* rgba,unsigned cache) {
    std::memset(rgba,0,4);
    if(!source.Present()) return;
    unsigned channels=layer==4?4:1;
    Sprite s{};
    if(source.bank!=255) {
        s.w=uint16_t(std::lround(source.rect.r-source.rect.x)); s.h=uint16_t(std::lround(source.rect.b-source.rect.y));
    } else s=pack_->GetSprite(source.sprite);
    float fx=Clamp(u)*s.w-.5f,fy=Clamp(v)*s.h-.5f;
    if(source.flip) fx=s.w-1-fx;
    fx=std::max(0.f,std::min(float(s.w-1),fx)); fy=std::max(0.f,std::min(float(s.h-1),fy));
    // Quantize coordinates before splitting integer/fractional parts. Values
    // infinitesimally below an exact pixel must not introduce a one-LSB seam
    // when the current layer is captured and immediately used at transition 0.
    unsigned sx=unsigned(std::lround(fx*256)),sy=unsigned(std::lround(fy*256));
    int x=int(sx>>8),y=int(sy>>8),x2=std::min(x+1,int(s.w)-1),y2=std::min(y+1,int(s.h)-1);
    unsigned wx=sx&255,wy=sy&255;
    const uint8_t* rows[2]{};
    if(source.bank!=255) {
        const auto* base=snapshots_+(size_t(source.bank)*8+(layer==4?4:layer))*kSide*kSide;
        rows[0]=base+y*kSide*channels; rows[1]=base+y2*kSide*channels;
    } else {
        auto& c=rows_[cache];
        if(c.sprite!=source.sprite) {c.sprite=source.sprite; c.row[0]=c.row[1]=-1;}
        const int ys[2]={y,y2};
        for(unsigned j=0;j<2;++j) {
            int slot=ys[j]&1;
            auto* target=CachedRow(cache,unsigned(slot));
            if(c.row[slot]!=ys[j]) { pack_->DecodeRow(source.sprite,unsigned(ys[j]),target,&c.span[slot]); c.row[slot]=ys[j]; }
            rows[j]=target;
        }
    }
    for(unsigned c=0;c<channels;++c) {
        unsigned a=rows[0][x*channels+c]*(256-wx)+rows[0][x2*channels+c]*wx;
        unsigned b=rows[1][x*channels+c]*(256-wx)+rows[1][x2*channels+c]*wx;
        rgba[channels==1?3:c]=uint8_t((a*(256-wy)+b*wy+32768)>>16);
    }
}
void Player::Mask(const Blend& blend,unsigned l,float x,float y,uint8_t* rgba) {
    std::memset(rgba,0,4);
    if(!Inside(blend.rect,x,y)) return;
    float u=(x-blend.rect.x)/(blend.rect.r-blend.rect.x),v=(y-blend.rect.y)/(blend.rect.b-blend.rect.y);
    uint8_t a[4],b[4]; Sample(blend.a,l,u,v,a,l*2); Sample(blend.b,l,u,v,b,l*2+1);
    rgba[3]=uint8_t(std::lround(Mix(a[3],b[3],blend.u)));
}
void Player::Accent(const Pose& pose,float x,float y,uint8_t* rgba) {
    uint8_t colors[2][4]{};
    for(unsigned i=0;i<2;++i) { const auto& s=pose.accent[i];
        if(s.Present() && Inside(s.rect,x,y)) Sample(s,4,(x-s.rect.x)/(s.rect.r-s.rect.x),
            (y-s.rect.y)/(s.rect.b-s.rect.y),colors[i],8+i);
    }
    for(unsigned c=0;c<4;++c) rgba[c]=uint8_t(std::lround(Mix(colors[0][c],colors[1][c],pose.accent_u)));
}
void Player::Freeze() {
    // Alternate banks: rapid interrupts can read the previous snapshot while
    // producing the next one. No read aliases a destination being overwritten.
    bank_^=1;
    Compile();
    for(unsigned l=0;l<5;++l) {
        Rect r{}; bool present=false;
        if(l<4) { r=pose_.eyes[l].rect; present=pose_.eyes[l].a.Present() || pose_.eyes[l].b.Present(); }
        else for(const auto& s:pose_.accent) if(s.Present()) {
            if(!present) r=s.rect;
            else r={std::min(r.x,s.rect.x),std::min(r.y,s.rect.y),std::max(r.r,s.rect.r),std::max(r.b,s.rect.b)};
            present=true;
        }
        if(!present) { frozen_[l]={}; continue; }
        r={std::floor(r.x),std::floor(r.y),std::ceil(r.r),std::ceil(r.b)};
        frozen_[l]={r,65535,0,bank_};
        auto* target=snapshots_+(size_t(bank_)*8+(l==4?4:l))*kSide*kSide;
        for(int y=0;y<int(r.b-r.y);++y) {
          const auto a=ScanRow(l<4?l*2:8,y+r.y+.5f),b=ScanRow(l<4?l*2+1:9,y+r.y+.5f);
          MaskSpan span{};unsigned solid_start=0;bool solid=false;
          for(int x=0;x<int(r.r-r.x);++x) {
            uint8_t pixel[4];
            if(l==4) { AccentValue(x+int(r.x),a,b,pixel); std::memcpy(target+(y*kSide+x)*4,pixel,4); }
            else {
                const auto alpha=MaskValue(l,x+int(r.x),a,b);target[y*kSide+x]=alpha;
                MaskRun(span,unsigned(x),1,alpha,solid_start,solid);
            }
          }
          if(l<4) snapshot_spans_[bank_][l][y]=span;
        }
    }
    std::memcpy(from_color_,pose_.color,3);
}
void Player::Begin(unsigned target,Phase phase) {
    Freeze(); current_=target; wanted_=target; clock_=0; phase_=phase; TransitionPose(0);
}
bool Player::Request(const char* semantic,bool replay) {
    int i=pack_?pack_->Find(semantic):-1; if(i<0) return false;
    if(!replay && unsigned(i)==wanted_) return true;
    if(unsigned(i)==idle_ || !std::strcmp(semantic,"neutral")) { Cancel(); return true; }
    Begin(unsigned(i),Phase::Entry); return true;
}
void Player::Cancel() {
    if(!pack_) return;
    if((phase_==Phase::Idle || phase_==Phase::Exit) && wanted_==idle_) return;
    Begin(idle_,Phase::Exit);
}
Source Player::OrientedRef(const Clip& clip,unsigned frame,unsigned layer) const {
    // Keep snapshots and targets in the same screen coordinates. Mirroring is
    // a property of target layers, never a late whole-frame flip. Swap logical
    // eyes as well so an interrupted left eye continues toward the left eye.
    unsigned original=clip.mirror && layer<4 ? (1-layer/2)*2+layer%2 : layer;
    Source ref=pack_->Ref(frame,original);
    if(clip.mirror && ref.Present()) {
        ref.rect={kSide-ref.rect.r,ref.rect.y,kSide-ref.rect.x,ref.rect.b};
        ref.flip^=1;
    }
    return ref;
}
void Player::BodyPose(float progress) {
    compiled_=false;
    auto c=pack_->GetClip(current_);
    // Dense authored frames occupy body-1 intervals. Hold the final pose for
    // its full 40 ms, then start the exit from that pose, never wrap to frame 0.
    const float t=pack_->ExitFromLast()?std::min(progress*c.body/float(c.body-1),1.f)*(c.count-1):progress*c.count;
    unsigned first=unsigned(t)%c.count,next=pack_->ExitFromLast()?std::min<unsigned>(first+1,c.count-1):(first+1)%c.count;
    float u=t-std::floor(t);
    for(unsigned l=0;l<4;++l) pose_.eyes[l]=Between(OrientedRef(c,c.first+first,l),OrientedRef(c,c.first+next,l),u);
    pose_.accent[0]=OrientedRef(c,c.first+first,4);pose_.accent[1]=OrientedRef(c,c.first+next,4);pose_.accent_u=u;
    std::memcpy(pose_.color,c.color,3);
}
void Player::TransitionPose(float progress) {
    compiled_=false;
    auto c=pack_->GetClip(current_);
    for(unsigned l=0;l<4;++l) {
        unsigned eye=l/2; bool pupil=l%2;
        float u=Ease((progress-(pupil?eye*35/360.f:.12f+eye*25/360.f))/(pupil?.75f:.8f));
        pose_.eyes[l]=Between(frozen_[l],OrientedRef(c,c.first,l),u);
    }
    pose_.accent[0]=frozen_[4];pose_.accent[1]=OrientedRef(c,c.first,4);
    pose_.accent_u=Ease((progress-.12f)/.8f);
    Hue(from_color_,c.color,Ease((progress-.08f)/.92f),pose_.color);
}
void Player::Evaluate() {
    if(phase_==Phase::Entry || phase_==Phase::Exit) TransitionPose(float(clock_)/pack_->TransitionMs());
    else BodyPose(float(clock_)/(pack_->GetClip(current_).body*40));
}
void Player::Advance(uint32_t elapsed) {
    if(!pack_) return;
    // Clamp only the caller's pathological elapsed range, never the authored
    // body. Paused wall time is excluded by the display adapter.
    elapsed=std::min<uint32_t>(elapsed,60000u);
    while(elapsed) {
        auto c=pack_->GetClip(current_);
        unsigned duration=(phase_==Phase::Entry || phase_==Phase::Exit)?pack_->TransitionMs():c.body*40;
        unsigned step=std::min(elapsed,duration-clock_); clock_+=step;elapsed-=step;Evaluate();
        if(clock_!=duration) break;
        clock_=0;
        if(phase_==Phase::Entry) {phase_=Phase::Body;BodyPose(0);}
        else if(phase_==Phase::Exit) {phase_=Phase::Idle;current_=idle_;BodyPose(0);}
        else { ++body_cycles_;
            if(c.loop || phase_==Phase::Idle) BodyPose(0);
            else { Freeze(); current_=idle_;phase_=Phase::Exit;TransitionPose(0); }
        }
    }
}
unsigned Player::Damage(const Pose& previous,Region* regions) const {
    if(!regions) return 0;
    if(std::memcmp(previous.color,pose_.color,3)) {
        regions[0]={0,0,kSide,kSide};return 1;
    }
    unsigned count=0;
    for(unsigned group=0;group<3;++group) {
        Rect bounds{};bool present=false;
        auto include=[&](Rect r) {
            if(r.r<=r.x || r.b<=r.y) return;
            if(!present) bounds=r;
            else bounds={std::min(bounds.x,r.x),std::min(bounds.y,r.y),std::max(bounds.r,r.r),std::max(bounds.b,r.b)};
            present=true;
        };
        for(const Pose* pose:{&previous,&pose_}) {
            if(group<2) for(unsigned l=group*2;l<group*2+2;++l) {
                const auto& blend=pose->eyes[l];
                if(blend.a.Present() || blend.b.Present()) include(blend.rect);
            }
            else for(const auto& source:pose->accent) if(source.Present()) include(source.rect);
        }
        if(!present) continue;
        const int left=std::max(0,int(std::floor(bounds.x))),top=std::max(0,int(std::floor(bounds.y)));
        const int right=std::min(kSide,int(std::ceil(bounds.r))),bottom=std::min(kSide,int(std::ceil(bounds.b)));
        if(right>left && bottom>top) regions[count++]={int16_t(left),int16_t(top),int16_t(right-left),int16_t(bottom-top)};
    }
    return count;
}
bool Player::Draw(int x,int y,int width,int height,uint16_t* out,int stride,bool swap) {
    if(!pack_ || !out || x<0||y<0||width<0||height<0||x+width>kSide||y+height>kSide||stride<width) return false;
    Compile();
    auto rgb565=[&](uint32_t rgb) {
        uint16_t p=uint16_t(((rgb&0xf8)<<8) | ((rgb&0xfc00)>>5) | ((rgb&0xf80000)>>19));
        return swap?uint16_t(p>>8 | p<<8):p;
    };
    // Each 16-bit lane stays below 65536. This is exactly floor(n/255),
    // including the original +127 rounding, for both red/blue in one word.
    auto mask_blend=[](uint32_t rgb,unsigned alpha,bool white) -> uint32_t {
        if(!alpha) return rgb;
        if(alpha==255) return white?0xffffffu:0u;
        const unsigned inverse=255-alpha;
        const uint32_t add=white?alpha*255:0;
        uint32_t rb=(rgb&0xff00ff)*inverse+add*0x10001+0x7f007f;
        rb=(rb+0x10001+((rb>>8)&0xff00ff))>>8;
        unsigned g=(((rgb>>8)&255)*inverse+add+127)/255;
        return (rb&0xff00ff) | (g<<8);
    };
    const uint32_t background_rgb=unsigned(pose_.color[0]) | unsigned(pose_.color[1])<<8 | unsigned(pose_.color[2])<<16;
    const uint16_t background=rgb565(background_rgb);
    for(int row=0;row<height;++row) {
        auto* dest=out+row*stride;std::fill(dest,dest+width,background);
        Scan scan[10]{};int left=x+width,right=x;
        for(unsigned slot=0;slot<10;++slot) {
            // A local left-eye tile must not decode right-eye rows, or vice
            // versa. The same rule applies to partial subtitle redraws.
            if(plans_[slot].end<=x || plans_[slot].first>=x+width) continue;
            scan[slot]=ScanRow(slot,y+row+.5f);
            if(scan[slot].a) {left=std::min(left,plans_[slot].first);right=std::max(right,plans_[slot].end);}
        }
        left=std::max(left,x);right=std::min(right,x+width);
        if(left>=right) continue;
        std::fill(line_+left,line_+right,background_rgb);
        if(scan[8].a || scan[9].a) for(int col=left;col<right;++col) {
            uint8_t a[4];AccentValue(col,scan[8],scan[9],a);
            if(a[3]) {
                uint32_t pixel=0;
                for(unsigned c=0;c<3;++c) pixel|=std::min(255u,unsigned(a[c])+(pose_.color[c]*(255-a[3])+127)/255)<<(c*8);
                line_[col]=pixel;
            }
        }
        for(unsigned eye=0;eye<2;++eye) {
            const unsigned w=eye*4,p=w+2;
            const auto white_bounds=MaskBounds(eye*2,scan[w],scan[w+1]);
            const auto pupil_bounds=MaskBounds(eye*2+1,scan[p],scan[p+1]);
            int a=right,b=left;
            for(unsigned s=w;s<w+4;++s) if(scan[s].a) {a=std::min(a,plans_[s].first);b=std::max(b,plans_[s].end);}
            a=std::max(a,left);b=std::min(b,right);
            for(int col=a;col<b;++col) {
                const auto alpha=[&](unsigned layer,const MaskSpan& bounds) {
#ifndef DESKBOT_FACE_REFERENCE_MASKS
                    if(col<bounds.first || col>=bounds.end) return uint8_t(0);
                    if(col>=bounds.solid_first && col<bounds.solid_end) return uint8_t(255);
#else
                    (void)bounds; // Native oracle: sample every pixel directly.
#endif
                    return MaskValue(layer,col,scan[layer*2],scan[layer*2+1]);
                };
                const unsigned white=alpha(eye*2,white_bounds);
                const unsigned raw_pupil=alpha(eye*2+1,pupil_bounds);
                // Each eye's pupil is clipped by its own white, including
                // closed eyes and transitions. The other eye is never a mask.
                const unsigned pupil=pack_->ClipPupils()?(raw_pupil*white+127)/255:raw_pupil;
                if(pupil==255) {line_[col]=0;continue;}
                if(white==255) {line_[col]=(255-pupil)*0x10101;continue;}
                uint32_t pixel=line_[col];
                if(white) pixel=mask_blend(pixel,white,true);
                if(pupil) pixel=mask_blend(pixel,pupil,false);
                line_[col]=pixel;
            }
        }
        for(int col=left;col<right;++col) dest[col-x]=rgb565(line_[col]);
    }
    return true;
}
}  // namespace deskbot_face
#if defined(__GNUC__) && !defined(__clang__)
#pragma GCC pop_options
#endif
