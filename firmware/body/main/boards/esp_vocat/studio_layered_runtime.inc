// Included inside the role player's namespace. All entry points hold Emote.
struct Layered {
    deskbot_face::Pack pack{};
    deskbot_face::Player player{};
    uint8_t* workspace=nullptr;
    Runtime* owner=nullptr;
    bool attached=false;
    bool clock_valid=false;
    bool full_damage=true;
    unsigned damage_count=0;
    gfx_anim_surface_area_t damage[3]{};
    uint64_t tick_us=0,period_us=0;
    uint32_t rendered_frames=0,draw_max_us=0,frame_us=0,frame_max_us=0;
    uint64_t draw_us=0;
};
Phase FacePhase(const Runtime& runtime) {
    switch(runtime.face->player.GetPhase()) {
        case deskbot_face::Phase::Entry: return Phase::Entry;
        case deskbot_face::Phase::Exit: return Phase::Exit;
        default: return Phase::Loop;
    }
}
bool FaceDigest(const uint8_t* data,size_t bytes,const char* expected) {
    if(!expected || std::strlen(expected)!=64) return false;
    uint8_t digest[32]; if(mbedtls_sha256(data,bytes,digest,0)) return false;
    constexpr char hex[]="0123456789abcdef";
    for(unsigned i=0;i<32;++i) if(expected[i*2]!=hex[digest[i]>>4] || expected[i*2+1]!=hex[digest[i]&15]) return false;
    return true;
}
void FaceDetached(void* context) {
    auto& face=*static_cast<Layered*>(context);
    face.attached=false;face.clock_valid=false;
}
esp_err_t FaceTick(void* context) {
    auto& face=*static_cast<Layered*>(context); auto& runtime=*face.owner;
    if(Blocked(runtime)) return ESP_ERR_INVALID_STATE;
    face.frame_max_us=std::max(face.frame_max_us,face.frame_us);face.frame_us=0;
    const uint64_t now=esp_timer_get_time();
    const uint32_t elapsed=face.clock_valid?uint32_t(std::min<uint64_t>((now-face.tick_us)/1000,60000)):40;
    // The GFX callback may be delayed by drawing/voice work. Preserve authored
    // action duration and fractional milliseconds, excluding paused wall time.
    face.tick_us=face.clock_valid?face.tick_us+uint64_t(elapsed)*1000:now;
    face.clock_valid=true;face.period_us+=uint64_t(elapsed)*1000;
    const auto previous=face.player.CurrentPose();
    face.player.Advance(elapsed);
    deskbot_face::Region regions[3];
    face.damage_count=face.player.Damage(previous,regions);
    for(unsigned i=0;i<face.damage_count;++i) face.damage[i]={regions[i].x,regions[i].y,regions[i].w,regions[i].h};
    if(face.full_damage) {face.damage_count=1;face.damage[0]={0,0,360,360};face.full_damage=false;}
    // Only the first processed frame acknowledges an activation. Drawing does
    // not call SetEmotion, touch the network, or retrigger a base action.
    const bool first=runtime.rendered_selection!=runtime.selection || runtime.rendered!=0;
    runtime.rendered=0; runtime.rendered_selection=runtime.selection;
    if(first && runtime.schedule) runtime.schedule(0);
    ++face.rendered_frames;
#ifdef ESP_PLATFORM
    if(face.rendered_frames==2 || (face.rendered_frames<250 && face.rendered_frames%50==0) || face.rendered_frames%250==0) ESP_LOGI("LayeredFace",
        "frames=%lu draw_avg_us=%lu draw_max_frame_us=%lu tick_avg_us=%lu internal_free=%u psram_free=%u",
        (unsigned long)face.rendered_frames,(unsigned long)(face.draw_us/face.rendered_frames),
        (unsigned long)face.frame_max_us,(unsigned long)(face.period_us/face.rendered_frames),
        unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT)),
        unsigned(heap_caps_get_free_size(MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)));
#endif
    return ESP_OK;
}
unsigned FaceDamage(void* context,gfx_anim_surface_area_t* areas) {
    auto& face=*static_cast<Layered*>(context);
    std::copy(face.damage,face.damage+face.damage_count,areas);return face.damage_count;
}
esp_err_t FaceDraw(void* context,int x,int y,int w,int h,uint16_t* out,int stride,bool swap) {
    auto& face=*static_cast<Layered*>(context);
    const uint64_t start=esp_timer_get_time();
    bool ok=face.player.Draw(x,y,w,h,out,stride,swap);
    uint32_t us=uint32_t(esp_timer_get_time()-start); face.draw_us+=us;
    face.frame_us+=us;
    face.draw_max_us=std::max(face.draw_max_us,us);
    return ok?ESP_OK:ESP_ERR_INVALID_ARG;
}
esp_err_t AttachFace(Runtime& runtime) {
    auto& face=*runtime.face;
    if(Blocked(runtime)) return ESP_OK;
    auto* eye=Eye(runtime.handle); if(!eye) return ESP_ERR_INVALID_STATE;
    const bool resuming=!face.attached || runtime.paused;
    if(!face.attached) {
        const gfx_anim_surface_t surface{&face,FaceTick,FaceDraw,FaceDetached,FaceDamage};
        auto result=gfx_anim_set_surface(eye,&surface,360,360,25);
        if(result!=ESP_OK) return result;
        face.attached=true;
        ESP_LOGI("LayeredFace","surface attached owners=%u visible=%u",unsigned(runtime.blocked),unsigned(gfx_obj_get_visible(eye)));
        if(gfx_obj_set_pos(eye,0,0)!=ESP_OK || gfx_obj_align(eye,GFX_ALIGN_CENTER,0,0)!=ESP_OK) return ESP_ERR_INVALID_STATE;
    }
    auto result=gfx_anim_start(eye);
    if(result==ESP_OK && (resuming || !gfx_obj_get_visible(eye))) result=gfx_obj_set_visible(eye,true);
    if(resuming && result==ESP_OK) {face.tick_us=esp_timer_get_time();face.clock_valid=true;face.full_damage=true;}
    runtime.paused=false;
    return result;
}
void PollFace(Runtime& runtime) {
    if(Blocked(runtime)) {
        if(!runtime.paused) ESP_LOGI("LayeredFace","paused owners=%u overlay=%u",unsigned(runtime.blocked),unsigned(Overlay(runtime.handle)));
        Stop(runtime); runtime.paused=true;
        runtime.face->clock_valid=false;
    }
    else if(AttachFace(runtime)!=ESP_OK) runtime.failed=true;
}
bool FaceRequest(Runtime& runtime,const char* semantic,bool replay,esp_err_t& result) {
    if(!semantic) return false;
    // Keep upstream cloud aliases on this role as well.
    const char* name=!std::strcmp(semantic,"sleep")?"sleepy":!std::strcmp(semantic,"scared")?"shocked":
        !std::strcmp(semantic,"microchip_ai")?"idle":semantic;
    // Startup/system icons keep their original EAF fallback. A role owns only
    // semantics present in its verified pack; the GFX detach callback records
    // replacement and the next role request reattaches the same player.
    auto& face=*runtime.face;
    const auto phase=face.player.GetPhase();const auto clock=face.player.Clock();
    const auto current=face.player.Current();
    if(!face.player.Request(name,replay)) return false;
    if(replay || current!=face.player.Current() || phase!=face.player.GetPhase() || clock!=face.player.Clock()) {
        face.tick_us=esp_timer_get_time();face.clock_valid=true;
    }
    result=AttachFace(runtime); runtime.failed=result!=ESP_OK;
    return true;
}
void ClearFace(Runtime& runtime) {
    auto* face=runtime.face; if(!face) return;
    if(auto* eye=Eye(runtime.handle)) { gfx_anim_stop(eye); gfx_anim_clear_surface(eye); }
    ESP_LOGI("LayeredFace","frames=%lu draw_total_us=%lu draw_max_us=%lu workspace=%u",
        (unsigned long)face->rendered_frames,(unsigned long)std::min<uint64_t>(face->draw_us,0xffffffffu),
        (unsigned long)face->draw_max_us,unsigned(deskbot_face::kSnapshotBytes+sizeof(Layered)));
    heap_caps_free(face->workspace); face->~Layered(); heap_caps_free(face);runtime.face=nullptr;
}
LoadResult LoadFace(Runtime& runtime) {
    const uint8_t* binary=nullptr;size_t bytes=0;
    if(emote_get_asset_data_by_name(runtime.handle,"face-motion.dfm",&binary,&bytes)!=ESP_OK) return LoadResult::Absent;
    const uint8_t* graph_raw=nullptr;size_t graph_bytes=0;
    if(emote_get_asset_data_by_name(runtime.handle,"deskbot-roles.json",&graph_raw,&graph_bytes)!=ESP_OK || graph_bytes>4096) return LoadResult::Invalid;
    deskbot_role_json::Document document;
    const auto* graph=document.Parse(graph_raw,graph_bytes);
    auto text=[](const deskbot_role_json::Node* n,const char* key) -> const char* {
        const auto* v=deskbot_role_json::GetObjectItemCaseSensitive(n,key);
        return deskbot_role_json::IsString(v)?v->valuestring:nullptr;
    };
    auto eq=[](const char* a,const char* b) {return a && b && !std::strcmp(a,b);};
    const auto* renderer=deskbot_role_json::GetObjectItemCaseSensitive(graph,"renderer");
    const auto* layout=deskbot_role_json::GetObjectItemCaseSensitive(graph,"layout");
    const auto* roles=deskbot_role_json::GetObjectItemCaseSensitive(graph,"roles");
    const auto* role=deskbot_role_json::GetArrayItem(roles,0);
    const uint8_t* layout_raw=nullptr;size_t layout_bytes=0;
    if(!eq(text(graph,"schema"),"deskbot.layered_role/v1") || !eq(text(graph,"default_role_id"),"airboat-face-v1") ||
       !eq(text(renderer,"file"),"face-motion.dfm") || !eq(text(renderer,"format"),"DFM1") ||
       !eq(text(renderer,"source_commit"),"22bb4746c0d115dea62b15ff5adf25725cb7d512") ||
       !FaceDigest(binary,bytes,text(renderer,"sha256")) || deskbot_role_json::GetArraySize(roles)!=1 ||
       !eq(text(role,"id"),"airboat-face-v1") || !eq(text(layout,"file"),"deskbot-layout.json") ||
       emote_get_asset_data_by_name(runtime.handle,"deskbot-layout.json",&layout_raw,&layout_bytes)!=ESP_OK ||
       !FaceDigest(layout_raw,layout_bytes,text(layout,"sha256"))) return LoadResult::Invalid;
    auto* mem=heap_caps_malloc(sizeof(Layered),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if(!mem) return LoadResult::Invalid;
    auto* face=new(mem) Layered{};
    runtime.face=face; face->owner=&runtime;
    face->workspace=static_cast<uint8_t*>(heap_caps_malloc(deskbot_face::kSnapshotBytes,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!face->pack.Open(binary,bytes) || !face->player.Init(face->pack,face->workspace,deskbot_face::kSnapshotBytes)) {
        ClearFace(runtime);return LoadResult::Invalid;
    }
    runtime.graph.roles=static_cast<Role*>(heap_caps_malloc(sizeof(Role),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT));
    if(!runtime.graph.roles) { ClearFace(runtime);return LoadResult::Invalid; }
    new(runtime.graph.roles) Role{};std::strcpy(runtime.graph.roles[0].id,"airboat-face-v1");
    runtime.graph.count=1;runtime.graph.initial=0;
    uint8_t digest[32];mbedtls_sha256(graph_raw,graph_bytes,digest,0);
    constexpr char hex[]="0123456789abcdef";
    for(unsigned i=0;i<32;++i) {runtime.graph.sha256[i*2]=hex[digest[i]>>4];runtime.graph.sha256[i*2+1]=hex[digest[i]&15];}
    ESP_LOGI("LayeredFace","360x360 25fps pack=%u workspace=%u",unsigned(bytes),unsigned(deskbot_face::kSnapshotBytes+sizeof(Layered)));
    return LoadResult::Applied;
}
