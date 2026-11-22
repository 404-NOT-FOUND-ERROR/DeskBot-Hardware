#include "studio_role_activation.h"
#include "studio_usb_session.h"
#include <cassert>
#include <iostream>
#include <string>
using namespace deskbot_role_activation;
using deskbot_role::Details;
Details Device() {
    Details d; d.loaded=true;d.count=2;d.initial=d.target=1;
    std::memset(d.graph_sha256,'a',64);
    std::strcpy(d.roles[0],"grok-round-personal-v1");std::strcpy(d.roles[1],"xiaozhi-e145");
    return d;
}
Session View() { return {"device-a","boot-b","session-b-1",1,30000,true}; }
std::string Op(unsigned i=0) { return "role.activate."+std::to_string(i)+"."+std::string(64,'a'); }
int main(int argc,char** argv) {
    assert(argc==2);const std::string mode=argv[1];
    Control control;auto device=Device();Saved saved{},target{};Reply reply;uint64_t token=0;
    control.Mount(device,nullptr);control.Sync(View(),0);
    if(mode=="decode") {
        uint8_t i=0;const char* hash=nullptr;
        assert(Activation(Op(3).c_str(),i,hash)&&i==3);
        for(const auto& op:{Op(4),"role.activate.00."+std::string(64,'a'),Op().substr(0,79),"role.activate.0."+std::string(64,'A')})
            assert(!Activation(op.c_str(),i,hash));
    } else if(mode=="dedup") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);assert(token&&std::string(reply.result)=="accepted");
        assert(!control.Submit(Op().c_str(),"click-1",11,reply)&&std::string(reply.result)=="duplicate");
        assert(!control.Submit(Op(1).c_str(),"click-1",12,reply)&&std::string(reply.reason)=="request_conflict");
        assert(control.Begin(token,20,target));assert(!control.Begin(token,21,target));
    } else if(mode=="expire") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);
        assert(!control.Begin(token,3010,target));control.Snapshot(reply);assert(std::string(reply.status)=="cancelled");
    } else if(mode=="disconnect") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);control.Disconnect();
        assert(!control.Begin(token,20,target));
    } else if(mode=="latest") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);
        const auto newer=control.Submit(Op(1).c_str(),"click-2",11,reply);
        assert(newer!=token&&!control.Begin(token,20,target)&&control.Begin(newer,20,target));
        control.Submit(Op().c_str(),"click-1",21,reply);assert(std::string(reply.status)=="cancelled");
    } else if(mode=="many") {
        for(unsigned i=1;i<=40;++i) {
            const auto request="click-"+std::to_string(i);
            token=control.Submit(Op(i%2).c_str(),request.c_str(),i,reply);assert(token);
        }
        assert(!control.Submit(Op(1).c_str(),"click-1",50,reply));
        assert(std::string(reply.reason)=="request_order_expired");
    } else if(mode=="unmount") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);assert(control.Begin(token,20,target));
        device.selection=22;device.target=0;control.Dispatched(token,device,true);
        device={};assert(!control.Observe(device,saved,token));control.Snapshot(reply);
        assert(std::string(reply.status)=="cancelled"&&std::string(reply.reason)=="roles_unavailable");
        assert(std::string(reply.save)=="none");
    } else if(mode=="frame-failure"||mode=="interrupted") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);assert(control.Begin(token,20,target));
        device.target=0;device.selection=22;
        control.Dispatched(token,device,true);
        device.failed=mode=="frame-failure";
        assert(!control.Observe(device,saved,token));control.Snapshot(reply);
        assert(std::string(reply.status)==(device.failed?"failed":"cancelled"));
        assert(std::string(reply.reason)==(device.failed?"frame_preparation_failed":"expression_interrupted"));
        assert(std::string(reply.save)=="none");
    } else if(mode=="first-frame"||mode=="save-failure"||mode=="reboot") {
        token=control.Submit(Op().c_str(),"click-1",10,reply);assert(control.Begin(token,20,target));
        device.target=0;device.selection=22;device.rendered=1;device.rendered_selection=21;
        device.current=1;device.phase=deskbot_role::Phase::Exit;
        control.Dispatched(token,device,true);
        assert(!control.Observe(device,saved,token));
        device.rendered=0;device.rendered_selection=21;assert(!control.Observe(device,saved,token));
        device.rendered_selection=22;assert(control.Observe(device,saved,token));
        assert(!std::strcmp(saved.role,device.roles[0]));
        const bool ok=mode!="save-failure";
        control.SavedResult(token,saved,ok);control.Snapshot(reply);
        assert(std::string(reply.status)=="applied"&&std::string(reply.save)==(ok?"saved":"failed"));
        assert(!control.Observe(device,saved,token));
        if(mode=="save-failure") {
            token=control.Submit(Op().c_str(),"click-2",30,reply);assert(control.Begin(token,40,target));
            control.Dispatched(token,device,true);assert(control.Observe(device,saved,token));
            control.SavedResult(token,saved,true);
        }
        if(mode=="reboot") {
            Control restored;assert(restored.Mount(Device(),&saved)==0);restored.Snapshot(reply);assert(std::string(reply.save)=="restored");
            auto changed=Device();changed.graph_sha256[0]='b';assert(restored.Mount(changed,&saved)==1);
            saved.version=2;assert(restored.Mount(Device(),&saved)==1);
        }
    } else if(mode=="unchanged") {
        std::strcpy(saved.graph,device.graph_sha256);std::strcpy(saved.role,device.roles[0]);
        control.Mount(device,&saved);token=control.Submit(Op().c_str(),"click-1",10,reply);assert(control.Begin(token,20,target));
        device.target=device.rendered=device.current=0;device.selection=device.rendered_selection=33;
        device.phase=deskbot_role::Phase::Held;
        control.Dispatched(token,device,true);assert(!control.Observe(device,saved,token));control.Snapshot(reply);assert(std::string(reply.save)=="saved");
    } else if(mode=="graph-index") {
        auto bad=Op();bad.back()='b';assert(!control.Submit(bad.c_str(),"click-1",10,reply)&&std::string(reply.reason)=="graph_changed");
        assert(!control.Submit(Op(2).c_str(),"click-2",11,reply)&&std::string(reply.reason)=="role_missing");
        token=control.Submit(Op().c_str(),"click-3",12,reply);device.loaded=false;control.Mount(device,nullptr);assert(!control.Begin(token,20,target));
    } else if(mode=="format"||mode=="format-max") {
        control.Snapshot(reply);char output[2048];
        assert(Format(reply,"query-1","device-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","boot-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "session-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-1",3,mode=="format"?100:9007199254740991ULL,output,sizeof(output)));
        std::cout<<output<<'\n';return 0;
    } else if(mode=="usb") {
        deskbot_studio_session::Session wire;
        assert(wire.Initialize(std::string(32,'a').c_str(),std::string(32,'b').c_str()));
        static unsigned calls=0;
        wire.SetRoleHooks({[](const char*,const char*,const Session&,uint64_t,Reply& out){++calls;out.device=Device();return true;},nullptr,nullptr});
        deskbot_studio_session::Request rq{};
        std::strcpy(rq.operation,"open");std::strcpy(rq.request_id,"req-1");
        std::strcpy(rq.target_id,("device-"+std::string(32,'a')).c_str());
        std::strcpy(rq.boot_id,("boot-"+std::string(32,'b')).c_str());
        std::strcpy(rq.client_nonce,"client-test");rq.session_null=true;char output[2048];
        assert(wire.Handle(rq,0,output,sizeof(output)));
        rq.session_null=false;std::strcpy(rq.session_id,("session-"+std::string(32,'b')+"-1").c_str());
        std::strcpy(rq.operation,"role.query");assert(wire.Handle(rq,400,output,sizeof(output))&&calls==1);
        std::strcpy(rq.client_nonce,"client-wrong");assert(wire.Handle(rq,800,output,sizeof(output))&&calls==1);
        std::strcpy(rq.client_nonce,"client-test");assert(wire.Handle(rq,31000,output,sizeof(output))&&calls==1);
    } else assert(false);
    std::cout<<"{\"result\":\"passed\"}\n";
}
