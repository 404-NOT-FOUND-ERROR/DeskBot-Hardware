#include "studio_role_player.h"
#include "studio_asset_layout.h"
#include "expression_emote/emote_api.h"
#include <cJSON.h>
#include <esp_heap_caps.h>
#include <mbedtls/platform_util.h>
#include <cassert>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#if DESKBOT_LAYERED_FACE
#include "studio_layered_gfx.h"
static gfx_anim_surface_t surface{};
static int64_t simulated_us=0;
static unsigned dirty_pixels=0,dirty_areas=0;
extern "C" int64_t esp_timer_get_time() { return simulated_us; }
#endif
using namespace deskbot_role;
namespace {
int handle, depth = 0;
unsigned allocations = 0, attempts = 0, plays = 0, images = 0, metadata = 0, refreshes = 0, observations = 0;
size_t json_attempts = 0, json_live = 0, json_peak = 0;
gfx_obj_t eye, dialog;
bool visible = true, overlay = false, playing = false;
const uint8_t* source = nullptr;
size_t source_bytes = 0;
uint32_t cursor = 0, begin = 0, end = 0, fps = 0;
std::string failure;
std::vector<uint64_t> queued;
std::map<std::string, std::vector<uint8_t>> files;
std::map<std::string, emoji_data_t> emojis;
void* JsonAlloc(size_t n) {
    const char* at = std::getenv("ROLE_JSON_FAIL");
    ++json_attempts;
    if (at && json_attempts == std::strtoul(at,nullptr,10)) return nullptr;
    auto* p=static_cast<size_t*>(std::malloc(n+sizeof(size_t)));
    if (!p) return nullptr;
    *p=n; json_live+=n; if (json_live>json_peak) json_peak=json_live;
    return p+1;
}
void JsonFree(void* raw) {
    if (!raw) return;
    auto* p=static_cast<size_t*>(raw)-1; assert(json_live>=*p); json_live-=*p; std::free(p);
}
uint32_t U32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1])<<8 | uint32_t(p[2])<<16 | uint32_t(p[3])<<24; }
bool Fail(const char* name) { if (failure != name) return false; failure.clear(); return true; }
void Pump() {
    assert(depth == 0);
    auto work = queued; queued.clear();
    for (auto token : work) Drain(&handle, token);
}
void Tick() {
#if DESKBOT_LAYERED_FACE
    simulated_us+=40000;
#endif
    extern void InstalledTick(); InstalledTick();
}
void Finish(bool pump) {
    for (unsigned i = 0; playing && i < 513; ++i) Tick();
    if (pump) Pump();
}
std::string SourceName() {
    for (const auto& row : files) if (row.second.data() == source) return row.first;
    return "";
}
void Print(int result) {
    auto snapshot = Read(&handle); assert(depth == 0);
    std::cout << "{";
#if DESKBOT_LAYERED_FACE
    std::cout << "\"dirty_pixels\":" << dirty_pixels << ",\"dirty_areas\":" << dirty_areas << ",";
#endif
#if CONFIG_DESKBOT_ROLE_ACTIVATION_ENABLE
    const auto d=Describe(&handle);
    std::cout << "\"graph\":\"" << d.graph_sha256 << "\",\"target\":" << int(d.target)
        << ",\"current\":" << int(d.current) << ",\"active\":" << int(d.rendered)
        << ",\"selection\":" << d.selection << ",\"rendered_selection\":" << d.rendered_selection
        << ",\"failed\":" << (d.failed?"true":"false") << ",\"sleep_known\":" << (d.sleep_known?"true":"false") << ",";
#endif
    std::cout << "\"loaded\":" << (snapshot.loaded ? "true" : "false")
        << ",\"phase\":" << int(snapshot.phase) << ",\"selected\":" << int(snapshot.selected)
        << ",\"blocked\":" << int(snapshot.blocked) << ",\"cursor\":" << snapshot.cursor
        << ",\"native_cursor\":" << cursor << ",\"playing\":" << (playing ? "true" : "false")
        << ",\"mirror\":" << (eye.mirror ? "true" : "false") << ",\"visible\":" << (visible ? "true" : "false") << ",\"result\":" << result
        << ",\"fps\":" << fps << ",\"plays\":" << plays << ",\"images\":" << images
        << ",\"metadata\":" << metadata << ",\"queued\":" << queued.size() << ",\"json_peak_bytes\":" << json_peak
        << ",\"observations\":" << observations
        << ",\"json_attempts\":" << json_attempts << ",\"json_live_bytes\":" << json_live
        << ",\"psram_attempts\":" << attempts
        << ",\"allocations\":" << allocations << ",\"source\":\"" << SourceName() << "\"}\n";
}
}
void catalog_log(const char*, const char*, ...) {}
void* heap_caps_malloc(size_t n, unsigned caps) {
    assert(caps == (MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    const char* at = std::getenv("ROLE_ALLOC_FAIL");
    ++attempts;
    if (at && attempts == std::strtoul(at, nullptr, 10)) return nullptr;
    auto* p = std::malloc(n); if (p) ++allocations; return p;
}
void heap_caps_free(void* p) { if (p) { assert(allocations); --allocations; std::free(p); } }
extern "C" void mbedtls_platform_zeroize(void* p, size_t n) { volatile auto* v = static_cast<volatile uint8_t*>(p); while (n--) *v++ = 0; }
esp_err_t emote_lock(emote_handle_t) { ++depth; return ESP_OK; }
esp_err_t emote_unlock(emote_handle_t) { assert(depth > 0); --depth; return ESP_OK; }
esp_err_t emote_get_asset_data_by_name(emote_handle_t, const char* n, const uint8_t** p, size_t* b) {
    assert(depth > 0); if (!files.count(n)) return ESP_ERR_INVALID_ARG;
    *p = files[n].data(); *b = files[n].size(); return ESP_OK;
}
esp_err_t emote_get_emoji_data_by_name(emote_handle_t, const char* n, emoji_data_t** p) {
    assert(depth > 0); if (!emojis.count(n)) return ESP_ERR_INVALID_ARG; *p = &emojis[n]; return ESP_OK;
}
gfx_obj_t* emote_get_obj_by_name(emote_handle_t, const char* n) {
    assert(depth > 0); if (!std::strcmp(n, "eye_anim")) return &eye;
    return !std::strcmp(n, "emerg_dlg") ? &dialog : nullptr;
}
esp_err_t gfx_anim_set_auto_mirror(gfx_obj_t* o, bool v) { assert(depth); o->mirror = v; return ESP_OK; }
esp_err_t gfx_obj_set_pos(gfx_obj_t* o, gfx_coord_t x, gfx_coord_t y) { assert(depth); o->x=x; o->y=y; return ESP_OK; }
esp_err_t gfx_obj_get_pos(gfx_obj_t* o, gfx_coord_t* x, gfx_coord_t* y) { assert(depth); *x=o->x; *y=o->y; return ESP_OK; }
esp_err_t gfx_obj_get_size(gfx_obj_t* o, uint16_t* w, uint16_t* h) { assert(depth); *w=o->width; *h=o->height; return ESP_OK; }
esp_err_t gfx_obj_align(gfx_obj_t* o, uint8_t align, gfx_coord_t x, gfx_coord_t y) {
    assert(depth); o->align=align; o->offset_x=x; o->offset_y=y; return ESP_OK;
}
esp_err_t emote_notify_all_refresh(emote_handle_t) { assert(depth); ++refreshes; return ESP_OK; }
esp_err_t gfx_anim_set_src(gfx_obj_t*, const void* raw, size_t n) {
    assert(depth); playing = false; if (Fail("src")) return ESP_ERR_INVALID_STATE;
#if DESKBOT_LAYERED_FACE
    gfx_anim_clear_surface(&eye);
#endif
    source=static_cast<const uint8_t*>(raw); source_bytes=n; cursor=0; ++plays; return ESP_OK;
}
#if DESKBOT_LAYERED_FACE
extern "C" esp_err_t gfx_anim_clear_surface(gfx_obj_t*) {
    assert(depth);auto previous=surface;surface={};if(previous.detached)previous.detached(previous.context);return ESP_OK;
}
extern "C" esp_err_t gfx_anim_set_surface(gfx_obj_t* obj,const gfx_anim_surface_t* next,uint16_t w,uint16_t h,uint8_t f) {
    assert(depth);playing=false;gfx_anim_clear_surface(obj);surface=*next;obj->width=w;obj->height=h;fps=f;source=nullptr;return ESP_OK;
}
#endif
esp_err_t gfx_anim_set_segment(gfx_obj_t*, uint32_t a, uint32_t b, uint32_t f, bool repeat) {
    assert(depth && !repeat); if (Fail("segment")) return ESP_ERR_INVALID_STATE;
    assert(source && b < U32(source+4) && a < b && f >= 1 && f <= 60);
    begin=cursor=a; end=b; fps=f; return ESP_OK;
}
esp_err_t gfx_anim_start(gfx_obj_t*) {
    assert(depth); if (Fail("start")) return ESP_ERR_INVALID_STATE; cursor=begin; playing=true; return ESP_OK;
}
esp_err_t gfx_anim_stop(gfx_obj_t*) { assert(depth); playing=false; return ESP_OK; }
esp_err_t gfx_obj_set_visible(gfx_obj_t*, bool value) { assert(depth); visible=value; return ESP_OK; }
bool gfx_obj_get_visible(gfx_obj_t* o) { assert(depth); return o == &dialog ? overlay : visible; }
esp_err_t emote_set_anim_emoji(emote_handle_t, const char* n) {
    assert(depth); if (!emojis.count(n)) return ESP_ERR_INVALID_ARG;
    auto& row=emojis[n]; return gfx_anim_set_src(&eye,row.data,row.size);
}
struct gfx_anim_t {
    bool is_playing, repeat; uint32_t current_frame, end_frame, start_frame;
#if DESKBOT_LAYERED_FACE
    gfx_anim_surface_t surface{};
#endif
};
struct gfx_core_context_t { struct { void (*update_cb)(gfx_core_context_t*, gfx_player_event_t, const void*); } callbacks; };
struct NativeObject {
    void* src; bool dummy; struct { bool is_visible; } state; gfx_core_context_t* parent_handle;
    struct { int x,y,width,height; } geometry;
};
static esp_err_t gfx_anim_prepare_frame(NativeObject*) {
    if (Fail("prepare")) return ESP_ERR_INVALID_STATE;
    assert(source_bytes >= 16);
    auto at=16+size_t(U32(source+4))*8+U32(source+20+cursor*8);
    const auto* frame=source+at;
    if (!std::memcmp(frame,"ZZ_S",4)) {
        ++images; eye.height=frame[14] | unsigned(frame[15])<<8;
        eye.width=eye.mirror?340:(frame[12] | unsigned(frame[13])<<8);
    } else ++metadata;
    return ESP_OK;
}
static void gfx_obj_invalidate(NativeObject*) {}
#if DESKBOT_LAYERED_FACE
struct gfx_area_t { int x1,y1,x2,y2; };
static void gfx_obj_calc_pos_in_parent(NativeObject*) {}
static void gfx_invalidate_area(gfx_core_context_t*,const gfx_area_t* r) {
    assert(r->x1>=0 && r->y1>=0 && r->x2<360 && r->y2<360 && r->x2>=r->x1 && r->y2>=r->y1);
    dirty_pixels+=unsigned(r->x2-r->x1+1)*(r->y2-r->y1+1);++dirty_areas;
}
#endif
#define ESP_LOGD(...) ((void)0)
#define gfx_obj_t NativeObject
#include "gfx_role_timer.inc"
#undef gfx_obj_t
namespace {
void InstalledTick() {
    if (!source
#if DESKBOT_LAYERED_FACE
        && !surface.tick
#endif
    ) return;
    auto update=[](gfx_core_context_t*, gfx_player_event_t event, const void*) {
        deskbot_asset_layout::OnFrame(&handle,event,&eye);
        OnFrame(&handle,event,&eye);
    };
    gfx_core_context_t context{{update}};
    gfx_anim_t anim{playing,false,cursor,end,begin};
#if DESKBOT_LAYERED_FACE
    anim.surface=surface;
#endif
    NativeObject obj{&anim,false,{visible && !overlay},&context,{0,0,360,360}};
#if DESKBOT_LAYERED_FACE
    dirty_pixels=dirty_areas=0;
#endif
    ++depth; gfx_anim_timer_callback(&obj); --depth;
    playing=anim.is_playing; cursor=anim.current_frame;
#if DESKBOT_LAYERED_FACE
    if(surface.draw && playing && visible && !overlay) {
        ++depth;uint16_t pixels[360];assert(surface.draw(surface.context,0,180,360,1,pixels,360,false)==ESP_OK);--depth;
    }
#endif
}
}
int main(int argc, char** argv) {
    assert(argc == 3);
    for (const auto& entry : std::filesystem::directory_iterator(argv[1])) if (entry.is_regular_file()) {
        std::ifstream input(entry.path(),std::ios::binary);
        files[entry.path().filename().string()]={std::istreambuf_iterator<char>(input),{}};
    }
    auto* index=cJSON_ParseWithLength(reinterpret_cast<const char*>(files["index.json"].data()),files["index.json"].size());
    assert(index);
    auto* collection=cJSON_GetObjectItemCaseSensitive(index,"emoji_collection");
    for(const auto* row=collection?collection->child:nullptr;row;row=row->next) {
        const auto* name=cJSON_GetObjectItemCaseSensitive(row,"name");
        const auto* file=cJSON_GetObjectItemCaseSensitive(row,"file");
        const auto* eaf=cJSON_GetObjectItemCaseSensitive(row,"eaf");
        const auto* speed=cJSON_GetObjectItemCaseSensitive(eaf,"fps");
        const auto* repeats=cJSON_GetObjectItemCaseSensitive(eaf,"loop");
        if(cJSON_IsString(name) && cJSON_IsString(file) && files.count(file->valuestring)) {
            const auto& raw=files.at(file->valuestring);
            emojis.emplace(name->valuestring,emoji_data_t{raw.data(),raw.size(),static_cast<uint8_t>(speed?speed->valueint:0),cJSON_IsTrue(repeats)!=0});
        }
    }
    cJSON_Delete(index);
#if DESKBOT_LAYERED_FACE
    // A system-only semantic exercises replacement by the legacy display owner.
    emojis.emplace("system_test",emojis.at("neutral"));
#endif
    assert(deskbot_asset_layout::Configure(&handle));
    cJSON_Hooks hooks{JsonAlloc,JsonFree}; cJSON_InitHooks(&hooks);
    auto schedule=[](uint64_t token) { assert(depth > 0); if(token) queued.push_back(token); else ++observations; };
    uint8_t owner_blocked = 0;
    Configure(&handle,schedule,owner_blocked);
    std::istringstream commands(argv[2]); std::string command;
    while (std::getline(commands,command,',')) {
        int result=ESP_OK;
        if (command.rfind("role:",0)==0) result=Select(&handle,command.c_str()+5)?0:1;
        else if (command=="legacy") {
            const auto& raw=files.at("winking.eaf");
            result=deskbot_asset_layout::PlayClip(&handle,raw.data(),raw.size(),U32(raw.data()+4),20);
        }
        else if (command=="tick") Tick();
#if DESKBOT_LAYERED_FACE
        else if(command.rfind("wait:",0)==0) simulated_us+=std::stoll(command.substr(5))*1000;
#endif
        else if (command=="finish") Finish(true);
        else if (command=="finish-queued") Finish(false);
        else if (command=="pump") Pump();
        else if (command=="reset") Reset(&handle);
        else if (command=="configure") Configure(&handle,schedule,owner_blocked);
        else if (command=="cancel") result=Cancel(&handle)?0:1;
        else if (command.rfind("system:",0)==0) {
            const auto* name=command.c_str()+7;
            if(!Request(&handle,name,result)) result=deskbot_asset_layout::Play(&handle,name);
        }
        else if (command.rfind("replay:",0)==0) { if(!Replay(&handle,command.c_str()+7,result))result=2; }
        else if (command.rfind("owners:",0)==0) {
            deskbot_studio_runtime::Snapshot owners{};
            if (command!="owners:unknown") {
                owners.page={1,static_cast<uint8_t>(deskbot_studio_runtime::Page::Home),deskbot_studio_runtime::Phase::Settled};
                owners.application={1,static_cast<uint8_t>(kDeviceStateIdle),deskbot_studio_runtime::Phase::Settled};
                if (command=="owners:menu") owners.page.value=static_cast<uint8_t>(deskbot_studio_runtime::Page::Other);
                if (command=="owners:listening") owners.application.value=kDeviceStateListening;
                if (command=="owners:speaking") owners.application.value=kDeviceStateSpeaking;
                if (command=="owners:transitioning") owners.application.phase=deskbot_studio_runtime::Phase::Transitioning;
                if (command=="owners:invalid") owners.application.value=255;
            }
            const auto mask=OwnerBlockers(owners);
            owner_blocked=(owner_blocked & static_cast<uint8_t>(Block::Sleep)) | mask;
            SyncOwners(&handle,mask); Poll(&handle);
        }
        else if (command=="overlay-on") { overlay=true; Poll(&handle); }
        else if (command=="overlay-off") { overlay=false; Poll(&handle); }
        else if (command.rfind("fail:",0)==0) failure=command.substr(5);
        else if (command=="menu-on" || command=="menu-off" || command=="listen-on" || command=="listen-off" || command=="sleep-on" || command=="sleep-off") {
            const auto reason=command.rfind("menu",0)==0 ? Block::Menu : command.rfind("listen",0)==0 ? Block::Listening : Block::Sleep;
            const uint8_t bit=static_cast<uint8_t>(reason);
            const bool value=command.size()>3 && command.substr(command.size()-3)=="-on";
            if (value) owner_blocked|=bit; else owner_blocked&=static_cast<uint8_t>(~bit);
            SetBlocked(&handle,reason,value);
        }
        else if (command=="other-event") { gfx_obj_t other; OnFrame(&handle,GFX_PLAYER_EVENT_ALL_FRAME_DONE,&other); }
        else { bool handled=Request(&handle,command.c_str(),result); if (!handled) result=2; }
        Print(result);
    }
    Reset(&handle); deskbot_asset_layout::Reset(&handle); Pump();
    assert(allocations==0 && json_live==0 && !playing && depth==0);
}
