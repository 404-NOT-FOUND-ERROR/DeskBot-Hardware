#include "studio_layered_face.inc"
#include <cassert>
#include <chrono>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
using namespace deskbot_face;
std::vector<uint8_t> Read(const char* file) {
    std::ifstream in(file,std::ios::binary);
    return {std::istreambuf_iterator<char>(in),std::istreambuf_iterator<char>()};
}
std::vector<uint16_t> Frame(Player& player,int tile=360) {
    std::vector<uint16_t> out(kSide*kSide);
    for(int y=0;y<kSide;y+=tile) assert(player.Draw(0,y,kSide,std::min(tile,kSide-y),out.data()+y*kSide,kSide));
    return out;
}
uint64_t Hash(const std::vector<uint16_t>& pixels) {uint64_t n=1469598103934665603ull;for(auto p:pixels)n=(n^p)*1099511628211ull;return n;}
int main(int argc,char** argv) {
    assert(argc>=2);
    auto raw=Read(argv[1]);Pack pack;
    if(!pack.Open(raw.data(),raw.size())) {std::cout<<"{\"valid\":false}\n";return 0;}
    if(argc>2 && std::string(argv[2])=="validate") {std::cout<<"{\"valid\":true}\n";return 0;}
    std::vector<uint8_t> workspace(kSnapshotBytes);Player player;
    assert(player.Init(pack,workspace.data(),workspace.size()));
    assert(!player.Init(pack,workspace.data(),workspace.size()-1));
    if(argc>2 && std::string(argv[2])=="damage-suite") {
        unsigned partial=0,checked=0;
        for(const char* name:{"idle","listening","thinking","happy","surprised","shocked","question","look_around",
            "winking","beat_swing","confused","relaxed","sleepy","laughing","funny","loving","embarrassed",
            "confident","delicious","sad","crying","silly","angry","insert","eat","look_left","look_right","tried","neutral"}) {
            assert(player.Request(name,true));
            for(unsigned step:{40,120,200,40,360,720,320,80}) {
                const Pose previous=player.CurrentPose();auto patched=Frame(player);
                player.Advance(step);Region regions[3];unsigned count=player.Damage(previous,regions);
                unsigned pixels=0;
                for(unsigned i=0;i<count;++i) {
                    const auto& r=regions[i];assert(r.x>=0 && r.y>=0 && r.w>0 && r.h>0 && r.x+r.w<=360 && r.y+r.h<=360);
                    pixels+=unsigned(r.w)*r.h;
                    assert(player.Draw(r.x,r.y,r.w,r.h,patched.data()+r.y*360+r.x,360));
                }
                assert(patched==Frame(player));
                if(pixels<360*360) ++partial;
                ++checked;
            }
            const Pose previous=player.CurrentPose();auto patched=Frame(player);player.Cancel();player.Advance(80);
            Region regions[3];unsigned count=player.Damage(previous,regions);
            for(unsigned i=0;i<count;++i) {const auto& r=regions[i];assert(player.Draw(r.x,r.y,r.w,r.h,patched.data()+r.y*360+r.x,360));}
            assert(patched==Frame(player));++checked;
        }
        assert(partial>29);
        std::cout<<"{\"valid\":true,\"damage_frames\":"<<checked<<",\"partial_frames\":"<<partial<<"}\n";return 0;
    }
    if(argc>2 && std::string(argv[2])=="suite") {
        assert(!player.Request("missing"));
        auto idle=Hash(Frame(player,17));
        assert(idle==Hash(Frame(player)));
        assert(!player.Draw(-1,0,1,1,reinterpret_cast<uint16_t*>(workspace.data()),1));
        assert(player.Request("happy"));
        auto start=Hash(Frame(player)); assert(idle==start);
        player.Advance(200);auto mid=Hash(Frame(player));assert(mid!=idle);
        auto clock=player.Clock();assert(player.Request("happy"));assert(clock==player.Clock());
        assert(player.Request("winking"));assert(Hash(Frame(player))==mid);
        player.Advance(80);auto wink=Hash(Frame(player));player.Cancel();assert(wink==Hash(Frame(player)));
        player.Advance(360);assert(player.GetPhase()==Phase::Idle);
        assert(Hash(Frame(player))==idle);
        assert(player.Request("happy",true));player.Advance(360);assert(player.GetPhase()==Phase::Body);
        player.Advance(1439);assert(player.GetPhase()==Phase::Body);player.Advance(1);assert(player.GetPhase()==Phase::Exit);
        assert(player.BodyCycles()==1);player.Advance(360);assert(player.GetPhase()==Phase::Idle);
        assert(player.Request("happy"));assert(player.GetPhase()==Phase::Idle); // Same status is not a replay.
        assert(player.Request("happy",true));assert(player.GetPhase()==Phase::Entry);
        player.Advance(120);assert(player.Request("crying",true));player.Advance(80);auto cry=Hash(Frame(player));
        assert(player.Request("confused",true));assert(Hash(Frame(player))==cry);
        player.Cancel();player.Advance(360);
        unsigned clips=0;
        for(const char* name:{"idle","listening","thinking","happy","surprised","shocked","question","look_around",
            "winking","beat_swing","confused","relaxed","sleepy","laughing","funny","loving","embarrassed",
            "confident","delicious","sad","crying","silly","angry","insert","eat","look_left","look_right","tried","neutral"}) {
            assert(player.Request(name,true));player.Advance(360);auto c=pack.GetClip(unsigned(pack.Find(name)));
            player.Advance(c.body*40); assert(c.loop?player.GetPhase()==Phase::Body || player.GetPhase()==Phase::Idle:player.GetPhase()==Phase::Exit);
            player.Cancel();player.Advance(360);assert(player.GetPhase()==Phase::Idle);++clips;
        }
        auto before=std::chrono::steady_clock::now();
        player.Request("winking",true);player.Advance(760);uint64_t checksum=Hash(Frame(player,16));
        auto us=std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now()-before).count();
        std::cout<<"{\"valid\":true,\"semantics\":"<<clips<<",\"interrupt_continuity\":true,\"cancel_continuity\":true,\"workspace_bytes\":"
            <<workspace.size()+sizeof(Player)<<",\"native_frame_us\":"<<us<<",\"checksum\":"<<checksum<<"}\n";
        return 0;
    }
    std::stringstream commands(argc>2?argv[2]:"");std::string op;
    while(std::getline(commands,op,',')) {
        if(op=="cancel") player.Cancel();
        else if(op.rfind("t:",0)==0) player.Advance(unsigned(std::stoul(op.substr(2))));
        else if(op.rfind("replay:",0)==0) assert(player.Request(op.substr(7).c_str(),true));
        else assert(player.Request(op.c_str()));
    }
    auto frame=Frame(player,16);
    if(argc>3) {
        std::ofstream out(argv[3],std::ios::binary);out<<"P6\n360 360\n255\n";
        for(auto p:frame){uint8_t c[3]={uint8_t(((p>>11)&31)*255/31),uint8_t(((p>>5)&63)*255/63),uint8_t((p&31)*255/31)};out.write(reinterpret_cast<char*>(c),3);}
    }
    std::cout<<"{\"valid\":true,\"phase\":"<<unsigned(player.GetPhase())<<",\"clock\":"<<player.Clock()<<",\"hash\":"<<Hash(frame)<<"}\n";
}
