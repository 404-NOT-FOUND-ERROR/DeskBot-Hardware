"""Add shell registration and binding views to the current node workbench."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shell_workbench_builder_parent', ROOT / 'scripts/build-deskbot-studio-e3f9d.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)
OUTPUT = ROOT / 'tmp/deskbot-shell-workbench-20261113'


def products():
    previous = parent.products(True)
    html = previous['index.html'].decode('utf-8')

    def once(old, new):
        nonlocal html
        if html.count(old) != 1:
            raise ValueError('外壳工作台入口已变化：' + old[:80])
        html = html.replace(old, new, 1)

    once('<span class="edition">E3f·9d</span>', '<span class="edition">角色与外壳</span>')
    once('<span>App E3f-7</span><span>资源 E1.45</span>', '<span>连接后查看版本</span><span>双角色包</span>')
    once('</head>', '<style>' + '\n'.join((ROOT / p).read_text('utf-8') for p in (
        'scripts/deskbot-shell-workbench.css', 'scripts/deskbot-characters.css', 'scripts/deskbot-shell-characters.css', 'scripts/deskbot-shell-runtime.css',
        'scripts/deskbot-workbench-system.css', 'scripts/deskbot-body-network.css')) + '</style></head>')
    once('<script id="studio-data"', '<script>' + '\n'.join((ROOT / p).read_text('utf-8') for p in (
        'scripts/deskbot-shell-workbench.js', 'scripts/deskbot-characters.js', 'scripts/deskbot-shell-runtime.js', 'scripts/deskbot-shell-installation.js', 'scripts/deskbot-shell-characters.js',
        'scripts/deskbot-workbench-motion.js', 'scripts/deskbot-body-network.js')) + '</script><script id="studio-data"')
    once("roles:'角色',connection:", "roles:'角色',characters:'我的角色',shells:'外壳',capture:'采样工具',connection:")
    once('<a href="#roles" data-view="roles">', '<a href="#characters" data-view="characters"><span data-icon="roles"></span><span>我的角色</span></a>\n        <a href="#roles" data-view="roles">')
    once('<a href="#connection" data-view="connection">', '<a href="#shells" data-view="shells"><span data-icon="device"></span><span>外壳</span></a>\n        <a href="#connection" data-view="connection">')
    once('  function rolesView(){return deviceRoles.view()', '  const shells=DeskBotShellWorkbench.create();\n  const shellCharacters=DeskBotShellCharacters.create();\n  const characters=DeskBotCharacters.create(D,JSON.parse($("runtime-roles-data").textContent));\n  function rolesView(){return deviceRoles.view()')
    once('  const shells=DeskBotShellWorkbench.create();', '  const bodyNetwork=DeskBotBodyNetwork.create({observation:()=>body.state()});\n  const shells=DeskBotShellWorkbench.create();')
    once('library,roles:rolesView,connection:', 'library,roles:rolesView,characters:()=>characters.view(),shells:()=>shellCharacters.view(),capture:()=>shells.view(),connection:')
    once('unifiedPackage.mount();deviceRoles.mount();', 'unifiedPackage.mount();deviceRoles.mount();shells.mount();characters.mount();shellCharacters.mount();bodyNetwork.mount();')
    once('  deviceRoles.init();', '  deviceRoles.init();\n  shells.init();\n  characters.init();\n  shellCharacters.init();')
    once('  shellCharacters.init();', '  shellCharacters.init();\n  bodyNetwork.init();')
    once('CONNECTION · E3c-6', 'CONNECTION')
    once('实时状态、平静表情和设备回执，一起查看。', '查看日常连接，更新电脑地址；USB 调试工具收在下方。')
    once('<div class="e3c-page"><div id="studio-control-slot">', '<div id="body-network-slot">${bodyNetwork.view()}</div><details class="bn-advanced"><summary>USB 调试与表情目录</summary><div class="e3c-page"><div id="studio-control-slot">')
    once('</div><details class="e3a-simulator">', '</div></details><details class="e3a-simulator">')
    once('deviceRoles:()=>deviceRoles.state(),', 'deviceRoles:()=>deviceRoles.state(),characters:()=>characters.state(),shells:()=>shells.state(),shellCharacters:()=>shellCharacters.state(),')
    once('shellCharacters:()=>shellCharacters.state(),', 'shellCharacters:()=>shellCharacters.state(),bodyNetwork:()=>bodyNetwork.state(),')
    once('function rolesView(){return deviceRoles.view()',
         "function rolesView(){return heading('EXPRESSION STUDIO','让角色的表达，连成一体。','预览表情、调整镜像与过渡，再打包发布。')+deviceRoles.view()")
    once('deviceRoles.view()+unifiedPackage.view()',
         'deviceRoles.view()+(globalThis.DESKBOT_LAYERED_FACE?`<section class="device-roles"><h2>高清分层表情</h2><p>当前圆屏使用下方的分层资源。选择表情，可预览完整动作、途中切换与收尾。</p><a href="/layered-face/" target="_blank" rel="noopener">打开完整预览与镜像选项 →</a><iframe title="高清分层表情预览" src="/layered-face/" style="width:100%;height:960px;border:0;border-radius:24px;margin-top:18px"></iframe></section>`:unifiedPackage.view())')
    once('function publishView(){return unifiedPackage.publishView();}',
         "function publishView(){return heading('PUBLISH','把创作，带到桌宠。','查看生成结果、资源余量和可下载的角色包。')+(globalThis.DESKBOT_LAYERED_FACE?`<section class=\"device-roles\"><h2>高清分层角色包</h2><p>角色包与当前分层播放器配套。镜像可在预览中调整并导出，重新打包后安装生效。</p><a href=\"/layered-face/\">预览与镜像选项 →</a> · <a href=\"/layered-face/assets-8m.bin\" download>下载当前资源包</a></section>`:unifiedPackage.publishView());}")
    once('<h1>选一个角色，看它如何表达。</h1>', '<h2>选一个角色，看它如何表达。</h2>')
    # Keep one route renderer. Visual transitions never reschedule business work.
    once('if(!reduced.matches&&document.startViewTransition){viewTransition?.skipTransition();viewTransition=document.startViewTransition(render);viewTransition.finished.catch(()=>{});}else render();',
         'DeskBotWorkbenchMotion.navigate(render);')
    once('if(simulatorOpen&&document.querySelector(".e3a-simulator"))document.querySelector(".e3a-simulator").open=true;',
         'if(simulatorOpen&&document.querySelector(".e3a-simulator"))document.querySelector(".e3a-simulator").open=true;\n    DeskBotWorkbenchMotion.afterRender(workspace,view);')
    once('mountIcons();render();if(initial!==D.initial)', 'DeskBotWorkbenchMotion.init();mountIcons();render();if(initial!==D.initial)')
    sources = ('protocol/shell_workbench.py', 'protocol/shell_workbench_http.py',
        'protocol/shell_capture_readiness.py',
        'protocol/shell_role_assignment.py', 'protocol/shell_profile_archive.py',
        'protocol/shell_repeatability.py', 'scripts/build-deskbot-shell-workbench.py',
        'scripts/serve-deskbot-shell-workbench.py', 'scripts/start-deskbot-shell-workbench.ps1',
        'scripts/deskbot-shell-workbench.js',
        'scripts/deskbot-shell-workbench.css', 'docs/shell-workbench-s1c-s2a.md',
        'docs/shell-field-guide-s1d.md', 'tests/test_shell_capture_readiness.py',
        'tests/test_shell_workbench.py', 'tests/shell-workbench-browser.cjs',
        'tests/serve_shell_workbench_fixture.py', 'protocol/character_profiles.py',
        'protocol/character_profiles_http.py', 'protocol/deskbot-role-profile-v2.schema.json',
        'scripts/deskbot-characters.js', 'scripts/deskbot-characters.css',
        'tests/test_character_profiles.py', 'tests/characters-browser.cjs',
        'docs/character-profiles-stage1.md', 'protocol/character_switch.py',
        'protocol/shell_characters.py', 'protocol/shell_characters_http.py',
        'scripts/deskbot-shell-characters.js', 'scripts/deskbot-shell-characters.css',
        'docs/shell-characters-stage2.md', 'tests/test_shell_characters.py', 'tests/shell-characters-browser.cjs',
        'protocol/shell_switch_adapter.py', 'protocol/shell_switch_lab.py',
        'protocol/studio_control_link.py', 'protocol/studio_control_bridge.py',
        'protocol/deskbot-studio-control-link-v1.schema.json',
        'docs/shell-switch-adapter-stage3a.md', 'tests/test_shell_switch_adapter.py',
        'protocol/magnetic_stream.py','protocol/shell_switch_runtime.py','protocol/shell_runtime_replay.py',
        'tools/observe-studio-body-e3c.py','docs/shell-switch-runtime-stage3b1.md',
        'tests/test_shell_switch_runtime.py', 'protocol/shell_switch_service.py', 'protocol/shell_switch_http.py',
        'scripts/deskbot-shell-runtime.js', 'scripts/deskbot-shell-runtime.css',
        'docs/shell-switch-service-stage3b2.md', 'tests/test_shell_switch_service.py',
        'tests/serve_shell_switch_fixture.py', 'tests/shell-runtime-browser.cjs',
        'scripts/deskbot-workbench-system.css', 'scripts/deskbot-workbench-motion.js',
        'docs/workbench-visual-system-20261116.md', 'tests/workbench-visual-browser.cjs',
        'protocol/shell_installation.py', 'protocol/shell_installation_http.py',
        'scripts/deskbot-shell-installation.js', 'docs/shell-installation-confirmation.md',
        'tests/test_shell_installation.py', 'tests/serve_shell_installation_fixture.py',
        'tests/shell-installation-browser.cjs', 'scripts/deskbot-body-network.js', 'scripts/deskbot-body-network.css',
        'protocol/body_network_workbench.py', 'scripts/configure-body-network.py', 'docs/body-network-workbench.md')
    sha = lambda raw: hashlib.sha256(raw).hexdigest()
    report = dict(edition='Owner installation confirmation / Unified workbench', result='shell_workbench_generated',
        scope='owner_confirmed_installation_character_profiles_shell_associations_and_offline_session_lab', parent_report_sha256=sha(previous['report.json']),
        sources=[dict(path=name, sha256=sha((ROOT / name).read_bytes())) for name in sources],
        hardware_accessed=False, device_written=False, firmware_changed=False)
    return {**previous, 'index.html': html.encode('utf-8'),
            'report.json': (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode('utf-8')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for name, raw in products().items():
        path = OUTPUT / name
        if args.check:
            if path.read_bytes() != raw:
                raise ValueError('工作台生成内容已变化：' + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
    print('Shell workbench ' + ('checked' if args.check else 'built'))


if __name__ == '__main__':
    main()
