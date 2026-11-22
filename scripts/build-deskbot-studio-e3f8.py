#!/usr/bin/env python3
"""Add persistent runtime roles to the existing E3f-7 workbench."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.role_activation import load_roles
from protocol.studio_resource_package import json_bytes, sha

spec = importlib.util.spec_from_file_location("role_activation_builder_parent", ROOT / "scripts/build-deskbot-studio-e3f7.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
LOCAL = ROOT / "tmp/deskbot-studio-e3f8-20261111"
PUBLIC = ROOT / "artifacts/deskbot-studio-e3f8-20261111"
SOURCES = ("scripts/build-deskbot-studio-e3f8.py", "scripts/serve-deskbot-studio-e3f8.py",
    "scripts/start-deskbot-studio-e3f8.ps1", "scripts/deskbot-runtime-roles-e3f8.js",
    "scripts/deskbot-runtime-roles-e3f8.css", "scripts/grok-role-scheduler.js",
    "scripts/deskbot-studio-light.css", "scripts/deskbot-role-pack-summary.js",
    "scripts/deskbot-mirror-options.js", "scripts/deskbot-mirror-options.css", "scripts/deskbot-mirror-render.js",
    "protocol/studio_mirror_options.py", "protocol/deskbot-studio-draft-v2.schema.json",
    "docs/workbench-mirror-node-20261111.md", "tests/studio-mirror-options.cjs",
    "tests/test_studio_mirror_options.py",
    "tests/serve_studio_mirror_fixture.py",
    "tests/studio-mirror-e3f8-browser.cjs",
    "docs/workbench-style-and-role-packs-20261111.md", "tests/studio-style-e3f8-browser.cjs",
    "artifacts/grok-role-clips-20261111/manifest.json", "artifacts/grok-round-eaf-20261111/manifest.json",
    "protocol/role_activation.py", "protocol/deskbot-expression-roles-v1.json", "docs/role-activation-e3f8.md",
    "tests/test_role_activation.py", "tests/test_deskbot_studio_e3f8.py", "tests/studio-roles-e3f8-browser.cjs")


def products(local=False):
    previous = parent.products(local)
    html = previous["index.html"].decode("utf-8")
    catalog, media = load_roles()
    projection = json.loads((ROOT / "artifacts/grok-role-clips-20261111/manifest.json").read_text("utf-8"))["capacity_projection"]
    library = json.loads((ROOT / "artifacts/grok-round-eaf-20261111/manifest.json").read_text("utf-8"))
    plan = dict(base_bytes=projection["base_package_bytes"], added_eaf_bytes=projection["added_eaf_bytes"],
        projected_bytes=projection["projected_package_bytes"], soft_budget_bytes=projection["soft_budget_bytes"],
        partition_bytes=projection["partition_bytes"], full_library_bytes=library["capacity"]["all_eaf_bytes"],
        scope="offline_capacity_projection", includes_future_role_graph=projection["includes_future_role_graph_schema"])

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError("E3f-8 workbench anchor changed: " + old[:110])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3f·7</span>', '<span class="edition">E3f·8</span>')
    once('</head>', '<style>' + '\n'.join((ROOT / path).read_text("utf-8") for path in (
        "scripts/deskbot-runtime-roles-e3f8.css", "scripts/deskbot-studio-light.css",
        "scripts/deskbot-mirror-options.css")) + '</style></head>')
    scripts = '\n'.join((ROOT / path).read_text("utf-8") for path in (
        "scripts/grok-role-scheduler.js", "scripts/deskbot-role-pack-summary.js",
        "scripts/deskbot-mirror-options.js", "scripts/deskbot-mirror-render.js", "scripts/deskbot-runtime-roles-e3f8.js"))
    once('<script id="studio-data"', '<script id="runtime-roles-data" type="application/json">' +
         json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c') +
         '</script><script id="role-pack-data" type="application/json">' + json.dumps(plan, separators=(',', ':')) +
         '</script><script>' + scripts + '</script>\n<script id="studio-data"')
    once('  const editor=Core.create(D,initial);', '  const editor=Core.create(D,initial);\n' +
         "  const runtimeRoles=DeskBotRuntimeRoles.create(D,JSON.parse($('runtime-roles-data').textContent)," +
         "{paint:()=>{observeCanvases();startRaf();}});\n" +
         "  const rolePacks=DeskBotRolePacks.create(JSON.parse($('role-pack-data').textContent));")
    once('  function rolesView(){', '  function rolesView(){return runtimeRoles.view()+rolePacks.view()+`<details class="runtime-draft-editor"><summary>角色草稿编辑 · 独立于当前启用角色</summary>${draftRolesView()}</details>`;}\n  function draftRolesView(){')
    once('  function publishView(){', '  function publishView(){return rolePacks.view()+draftPublishView();}\n  function draftPublishView(){')
    once('aria-label="当前角色"', 'aria-label="正在编辑的角色草稿"')
    once('    syncPlayback();startRaf();\n    if(simulatorOpen', '    syncPlayback();startRaf();runtimeRoles.mount();\n    if(simulatorOpen')
    once('  roleOverview.init();', '  roleOverview.init();\n  runtimeRoles.init();')
    once('roleOverview:()=>roleOverview.state(),', 'roleOverview:()=>roleOverview.state(),runtimeRoles:()=>runtimeRoles.state(),')
    once('<div class="top-actions">', '<div class="top-actions"><a class="active-role-link" id="active-role-label" href="#roles" data-navigate="roles">选择电脑角色</a>')
    once('<div class="device-version"><span>App E1.42</span><span>资源 E1.18</span></div>',
         '<div class="device-version"><span>App E3f-7</span><span>资源 E1.45</span></div>')
    # Extend only the current editor. The earlier v1 core remains reproducible.
    core = (ROOT / "scripts/deskbot-studio-e2a-core.js").read_text("utf-8")
    upgraded = core

    def core_once(old, new):
        nonlocal upgraded
        if upgraded.count(old) != 1:
            raise ValueError("mirror core anchor changed: " + old[:80])
        upgraded = upgraded.replace(old, new, 1)

    core_once("const SCHEMA = 'deskbot.studio_draft/v1'", "const SCHEMA = DeskBotMirrorOptions.SCHEMA")
    core_once("  function validate(value, data) {", "  function validate(value, data) {\n    value=DeskBotMirrorOptions.migrate(value);")
    core_once("keys(role,['id','name','voice','example','bindings'])", "keys(role,['id','name','voice','example','bindings','mirror_overrides'])")
    core_once("      ids.add(role.id);", "      ids.add(role.id);DeskBotMirrorOptions.validate(role.mirror_overrides,data);")
    core_once(".map(([semantic,resource])=>({semantic,before:data.semantics[semantic].resource_id,after:resource}));",
              ".map(([semantic,resource])=>({semantic,before:data.semantics[semantic].resource_id,after:resource})).concat(DeskBotMirrorOptions.differences(role,data));")
    core_once("    const exact=changes.length===0 || exactCandidate;", "    const exact=false;")
    core_once("const bytes=exactCandidate ? release.candidate.package_bytes : release.rollback_reference.package_bytes +\n      extras.reduce((n,id)=>n+data.assets[id].bytes,0)+(changes.length?4096:0);",
              "const bytes=(exactCandidate ? release.candidate.package_bytes : release.rollback_reference.package_bytes +\n      extras.reduce((n,id)=>n+data.assets[id].bytes,0)+(changes.length?4096:0))+12288;")
    core_once("changes.length?'review_required':'baseline'", "'review_required'")
    core_once("'verified_candidate':", "'review_required':")
    core_once("      rename(name){", """      setMirror(resource,mirror){
        if(!own(data.assets,resource)||data.assets[resource].object_kind==='listen'||(mirror!==null&&typeof mirror!=='boolean'))throw Error('镜像设置无效');
        return commit(next=>{const role=next.roles.find(r=>r.id===next.selected_role_id);if(mirror===null)delete role.mirror_overrides[resource];else role.mirror_overrides[resource]=mirror;});
      },
      rename(name){""")
    once(core, upgraded)
    once("if(canvasEl.dataset.layout==='eye_anim'){const cw=Math.min(w,340),top=Math.floor((360-h)/2)+10;ctx.drawImage(image,x,y,cw,h,10,top,cw,h);ctx.translate(350,0);ctx.scale(-1,1);ctx.drawImage(image,x,y,cw,h,0,top,cw,h);}",
         "if(canvasEl.dataset.layout==='eye_anim')DeskBotMirrorRender.draw(ctx,image,x,y,w,h);")
    # GFX centers integer-sized frames with integer division. Odd-width eyes
    # must land on whole pixels when the user turns mirroring off.
    once("(360-fw)/2,canvasEl.dataset.layout==='listen_anim'?25:(360-fh)/2,fw,fh",
         "Math.floor((360-fw)/2),canvasEl.dataset.layout==='listen_anim'?25:Math.floor((360-fh)/2),fw,fh")
    release_core = (ROOT / "scripts/deskbot-studio-e2b-release-core.js").read_text("utf-8")
    once(release_core, release_core + "\nDeskBotStudioReleaseCore=DeskBotMirrorOptions.installRelease(DeskBotStudioReleaseCore);\n")
    once("      Core.validate(value,D);", """      Core.validate(value,D);
      if(value.layout_manifest){
        const bytes=new TextEncoder().encode(JSON.stringify(value.layout_manifest.value,null,2)+'\\n');
        const actual=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(v=>v.toString(16).padStart(2,'0')).join('');
        if(actual!==value.layout_manifest.sha256)throw Error('镜像清单摘要与内容不一致');
      }""")
    once("const action=s.action_id,stats=timing(current,semantic);",
         "const action=s.action_id,stats=timing(current,semantic),mirrorInfo=DeskBotMirrorOptions.resolve(D,current,semantic,active().mirror_overrides);")
    once("节点可以拖动。选择新动画时，旧连接与新连接会同时保留作对照。",
         "选择动画，调整镜像，直接预览圆屏效果。修改自动保存到当前角色草稿。")
    once("${chip(changed?'草稿已修改':'沿用设备快照',changed?'amber':'')}",
         "${chip(changed?'草稿已修改':mirrorInfo.manual?'镜像已手动设置':'沿用素材默认',(changed||mirrorInfo.manual)?'amber':'')}")
    once("当前只调整“${esc(label(semantic))}”在“${esc(active().name)}”里的动画连接。",
         "替换动画调整当前表情的连接；镜像选择用于当前角色中引用该素材的所有表情。")
    once('''<article class="node node-resource" id="node-resource" data-node="resource">${nodeHead('03 · 动画资源')}<i class="node-port port-in"></i><div class="node-canvas">${canvas(current,{animate:true})}</div>''',
         '''<article class="node node-resource" id="node-resource" data-node="resource">${nodeHead('03 · 动画资源')}<i class="node-port port-in"></i><div class="node-canvas">${canvas(current,{animate:true})}</div>${DeskBotMirrorOptions.controls(D,current,semantic,active())}<details class="node-source"><summary>查看原片 · ${a?.frame_width||'—'} × ${a?.frame_height||'—'}</summary><div class="node-canvas">${canvas(current,{animate:true,layout:'raw'})}</div></details>''')
    once("${chip((D.asset_layouts[current]||s.layout_id)==='full_frame'?'完整画面 · 无镜像':s.layout_id==='listen_anim'?'聆听布局':'双眼镜像')}",
         "${chip(mirrorInfo.title+' · '+(mirrorInfo.manual?'手动':'自动'))}")
    once("const mode=layout||D.asset_layouts[id]||D.semantics[semanticId]?.layout_id||(D.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');",
         "const mode=layout||DeskBotMirrorOptions.resolve(D,id,semanticId,active().mirror_overrides).id;")
    once("const layout=id=>data.asset_layouts[id]|| (data.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');",
         "const automaticLayout=id=>data.asset_layouts[id]||(data.assets[id]?.object_kind==='listen'?'listen_anim':'eye_anim');\n    const layout=id=>DeskBotMirrorOptions.resolve(data,id,semantic,role().mirror_overrides).id;")
    once("entry.layout_id!==layout(entry.resource_id)", "entry.layout_id!==automaticLayout(entry.resource_id)")
    once("[role().id,role().name,semantic,resource,result?.status]", "[role().id,role().name,semantic,resource,layout(resource),result?.status]")
    once("[source,semantic,resource,before,manifest?.catalog_id]", "[source,semantic,resource,layout(resource),before,manifest?.catalog_id]")
    once("    try{\n      if(event.target.id==='role-picker')", """    try{
      if(event.target.matches('[data-resource-mirror]')){editor.setMirror(event.target.dataset.resourceMirror,event.target.checked);changed('镜像设置已保存。');}
      if(event.target.id==='role-picker')""")
    once("  $('main-nav').addEventListener('click'", """  document.addEventListener('click',event=>{const target=event.target.closest('[data-mirror-auto]');if(target&&!target.disabled){editor.setMirror(target.dataset.mirrorAuto,null);changed('已恢复动画的自动镜像设置。');}});
  $('main-nav').addEventListener('click'""")
    once("active().bindings[id]!==s.resource_id?", "(active().bindings[id]!==s.resource_id||Object.hasOwn(active().mirror_overrides,active().bindings[id]))?")
    once("const draft=host.draft(),role=draft.roles.find(r=>r.id===draft.selected_role_id),estimate=host.budget(),",
         "const draft=host.draft(),role=draft.roles.find(r=>r.id===draft.selected_role_id),mirrorChanges=DeskBotMirrorOptions.differences(role,D),estimate=host.budget(),")
    once('''<section class="package-toolbar surface">''', '''<section class="mirror-release-note"><strong>镜像设置随包保存</strong> · 自动默认与手动选择写入布局清单，设备在 E3f-9 接入后采用。<div class="mirror-review-list">${mirrorChanges.map(c=>`<div><code>${esc(c.resource)}</code><span>${c.after?'镜像':'不镜像'} · 手动</span></div>`).join('')||'<span>使用各动画的自动默认值</span>'}</div></section><section class="package-toolbar surface">''')
    once("当前角色沿用已验收的映射，生成结果可与 E1.18 逐字节对照。", "资源连接沿用原映射；镜像清单按当前选择生成。")
    once("'新增动画成员'", "'新增资源成员'")
    once("'圆屏布局与聆听保持一致'", "'镜像选择已写入清单，聆听保留系统布局'")
    once("key=id+':'+n+':'+canvasEl.dataset.layout;",
         "key=id+':'+n+':'+canvasEl.dataset.layout;\n    canvasEl.dataset.frame=String(n);")
    once('''return `<canvas width="360" height="360" class="${cls}" data-resource="''',
         '''return `<canvas width="${mode==='raw'?(D.assets[id]?.frame_width||360):360}" height="${mode==='raw'?(D.assets[id]?.frame_height||360):360}" class="${cls}" data-resource="''')
    once("${esc(D.assets[id]?.label||'尚未接入动画')}圆屏参考预览",
         "${esc(D.assets[id]?.label||'尚未接入动画')}${mode==='raw'?'原片':'圆屏参考'}预览")
    once("    painted.set(canvasEl,key);ctx.clearRect(0,0,360,360);",
         """    painted.set(canvasEl,key);
    // A source node preserves every source pixel; only the layout output is circle-clipped.
    if(canvasEl.dataset.layout==='raw'&&a&&image){
      const w=a.frame_width,h=a.frame_height;ctx.clearRect(0,0,w,h);ctx.fillStyle='#000';ctx.fillRect(0,0,w,h);
      ctx.drawImage(image,n%a.columns*w,Math.floor(n/a.columns)*h,w,h,0,0,w,h);return;
    }
    ctx.clearRect(0,0,360,360);""")
    # pause() refreshes the slider; retain the user's chosen frame before that refresh.
    once("if(event.target.id==='frame-control'){pause();time=Number(event.target.value)/timing(resource(),semantic).fps;paintAnimated();}",
         "if(event.target.id==='frame-control'){const selectedFrame=Number(event.target.value);pause();time=(selectedFrame+.5)/timing(resource(),semantic).fps;paintAnimated();}")
    # Seek to the middle of a frame so floating point rounding cannot select its predecessor.
    once("time=((currentFrame()+1)%a.frame_count)/timing(a.id,semantic).fps;",
         "time=(((currentFrame()+1)%a.frame_count)+.5)/timing(a.id,semantic).fps;")
    sources = sorted({row["path"] for row in json.loads(previous["report.json"])["sources"]} | set(SOURCES))
    report = dict(edition="E3f-8", result="persistent_two_role_workbench_generated",
        role_catalog_id=catalog["catalog_id"], role_ids=[r["id"] for r in catalog["roles"]],
        parent_report=sha(previous["report.json"]),
        sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in sources],
        hardware_accessed=False, device_written=False, firmware_changed=False,
        activation_scope="computer", device_integration="next_increment",
        visual_style="light_node_preview", expression_graph="editable_resource_mirror", role_pack_capacity=plan,
        automatic_expression_commands=False)
    notices = ROOT / "artifacts/grok-role-clips-20261111/source-notices"
    return {**previous, "index.html": html.encode("utf-8"), "report.json": json_bytes(report),
            "role-catalog.json": json_bytes(catalog), **{name: path.read_bytes() for name, path in media.items()},
            **{"source-notices/" + p.name: p.read_bytes() for p in sorted(notices.iterdir()) if p.is_file()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    folder = LOCAL if args.local else PUBLIC
    for name, raw in products(args.local).items():
        if args.check:
            if (folder / name).read_bytes() != raw:
                raise ValueError("generated E3f-8 product differs: " + name)
        else:
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(raw)
    print("E3f-8 workbench " + ("checked" if args.check else "built"))
