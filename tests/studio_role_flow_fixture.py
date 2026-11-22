"""Production host/HTTP/C++ integration boundaries; all device bytes are in RAM."""
from copy import deepcopy
from http.client import HTTPConnection
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import threading
import unittest
from unittest.mock import patch

from protocol import studio_app_deployment as app
from protocol import studio_e3d2b_deployment as baseline
from protocol import studio_e3f1_deployment as shy
from protocol import studio_installed_catalog as directory
from protocol import studio_resource_installation as resources
from protocol import studio_resource_package as packages
from protocol import studio_control_link as control

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


native = load('role_flow_native_helpers', 'tests/test_studio_expression_control.py')
catalog_native = load('role_flow_catalog_helpers', 'tests/test_studio_catalog_native.py')
host = load('role_flow_host_helpers', 'tests/test_studio_control_link.py')
service = load('role_flow_http_service', 'scripts/serve-deskbot-studio-e3f2.py')
source_check = load('role_flow_source_check', 'scripts/build-expression-e1-43b-e3c3a.py')


class NativeBuild(unittest.TestCase):
    @classmethod
    def build(cls, root, enabled):
        cls.SHY_ENABLED = enabled
        # Extend only the test harness: the frozen production sources remain
        # byte-identical to the retained firmware build reports.
        harness = ROOT / 'tests/cpp/studio_expression_control_test.cc'
        source = harness.read_text(encoding='utf-8')
        substitutions = {
            '#include "studio_usb_session.h"': '#include "studio_usb_session.h"\n#include "studio_role_flow_provider.h"',
            '    Ready();\n    deskbot_studio_session::Endpoint endpoint;':
                '    if (argc != 4 || !role_flow::Load(argv[1])) return 7;\n    Ready();\n    deskbot_studio_session::Endpoint endpoint;',
            "const std::string device(32, 'a'), boot(32, 'b');":
                "const std::string device(32, 'a'), boot(32, argv[2][0]);",
            '    endpoint.SetDisplayProvider(deskbot_studio_runtime::ReadDisplay);':
                '    endpoint.SetCatalogProvider(role_flow::Read);\n    endpoint.SetDisplayProvider(deskbot_studio_runtime::ReadDisplay);',
            'size_t position = 0, capacity = 1024;': 'size_t position = 0, capacity = 2048;',
            "char reply[1025]{}; reply[1024] = 'z';": "char reply[2049]{}; reply[2048] = 'z';",
            "if (reply[1024] != 'z')": "if (reply[2048] != 'z')",
        }
        for before, after in substitutions.items():
            if source.count(before) != 1:
                raise ValueError('flow_native_harness_anchor_changed')
            source = source.replace(before, after)
        run = subprocess.run

        def compile_combined(command, **kwargs):
            command = list(command)
            if str(harness) in command:
                target = cls.folder / 'role-flow.cc'
                target.write_text(source, encoding='utf-8')
                config = cls.folder / 'sdkconfig.h'
                with config.open('a', encoding='utf-8') as stream:
                    stream.write('#define CONFIG_DESKBOT_STUDIO_CATALOG_ENABLE 1\n')
                command[command.index(str(harness))] = str(target)
                command += ['-I', str(ROOT / 'tests/cpp')]
            return run(command, **kwargs)

        with patch.multiple(native, MAIN=root / 'main', BOARD=root / 'main/boards/esp_vocat'), \
             patch.object(native.subprocess, 'run', side_effect=compile_combined):
            native.ExpressionControlTests.setUpClass.__func__(cls)


class OriginalBuild(NativeBuild):
    pass


class ShyBuild(NativeBuild):
    pass


class CatalogBuild(unittest.TestCase):
    compile = catalog_native.CatalogNativeTests.__dict__['compile']


def verified_builds():
    """Only select programs tied to the installed App and its retained sources."""
    root = ROOT / 'tmp/studio-shy-e3f1/p'
    build, _, candidate = shy.inputs()
    original = json.loads((ROOT / baseline.BUILD_REPORT).read_bytes())
    if app.sha((ROOT / baseline.BUILD_REPORT).read_bytes()) != baseline.BUILD_REPORT_SHA:
        raise ValueError('flow_original_build_changed')
    checked = {}
    for folder, record in ((ROOT / baseline.PROJECT, original), (root, build)):
        # The existing verifier normalizes only the reviewed local component
        # path in dependencies.lock; every source and header is checked.
        source_check.verify_frozen_sources(folder, record['source_sha256'])
        for name in record['source_sha256']:
            raw = (folder / name).read_bytes()
            checked[(folder / name).relative_to(ROOT).as_posix()] = app.sha(raw)
    try:
        OriginalBuild.build(ROOT / baseline.PROJECT, False)
        ShyBuild.build(root, True)
        with patch.object(catalog_native, 'BOARD', root / 'main/boards/esp_vocat'):
            catalog_native.CatalogNativeTests.setUpClass.__func__(CatalogBuild)
    except BaseException:
        close_builds()
        raise
    return candidate, checked


def close_builds():
    for cls in (CatalogBuild, ShyBuild, OriginalBuild):
        cls.doClassCleanups()


class Flash:
    def __init__(self, snapshot):
        self.raw = bytearray(snapshot)
        self.writes, self.boots = [], []
        self.fail_resource = self.fail_app = False

    def app_edition(self):
        if app.sha(bytes(self.raw[app.START:app.START + baseline.APP_SIZE])) == baseline.APP_SHA:
            return 'E3d-2b'
        if app.sha(bytes(self.raw[app.START:app.START + shy.APP_BYTES])) == shy.APP_SHA:
            return 'E3f-1'
        raise ValueError('flow_installed_app_unknown')

    def executable(self):
        return ShyBuild.exe if self.app_edition() == 'E3f-1' else OriginalBuild.exe


class ResourceDevice:
    simulation_only = True

    def __init__(self, flash, plan):
        self.flash, self.plan = flash, plan

    def identity(self):
        return self.plan.metadata['identity_sha256']

    def read_full(self):
        return bytes(self.flash.raw)

    def write_assets(self, raw):
        self.flash.writes.append(('resources', resources.START, len(raw)))
        if self.flash.fail_resource:
            self.flash.fail_resource = False
            self.flash.raw[resources.START:resources.START + 128] = raw[:128]
            raise OSError('injected_resource_write')
        self.flash.raw[resources.START:resources.START + resources.SIZE] = raw

    def boot(self):
        self.flash.boots.append(self.flash.app_edition())
        return True


class AppDevice:
    simulation_only = True

    def __init__(self, flash, plan):
        self.flash, self.plan = flash, plan

    def identity(self):
        return self.plan.metadata['identity_sha256']

    def verify_original(self):
        app.compare(self.plan, bytes(self.flash.raw), self.plan.rollback)
        return True

    def write_app(self, raw):
        self.flash.writes.append(('app', app.START, len(raw)))
        if self.flash.fail_app:
            self.flash.fail_app = False
            self.flash.raw[app.START:app.START + len(raw)//2] = raw[:len(raw)//2]
            raise OSError('injected_app_write')
        self.flash.raw[app.START:app.START + len(raw)] = raw

    def verify_image(self, raw, *, allow_runtime_nvs):
        return app.compare(self.plan, bytes(self.flash.raw), raw, allow_runtime_nvs=allow_runtime_nvs)

    def boot(self, candidate):
        edition = self.flash.app_edition()
        if (edition == 'E3f-1') != candidate:
            raise ValueError('flow_app_boot_mismatch')
        self.flash.boots.append(edition)
        return True


def installed_pages(flash, folder):
    """Feed the actual installed package to the production C++ catalog builder."""
    raw = packages.extract_package(bytes(flash.raw[resources.START:resources.START + resources.SIZE]), resources.SIZE)
    parser, _ = packages.load_legacy_tools()
    members = parser.parse_package_bytes(raw, modern=False)['members']
    folder.mkdir()
    for name, content in {**members, 'package.bin': raw}.items():
        if Path(name).name != name:
            raise ValueError('flow_member_path_invalid')
        (folder / name).write_bytes(content)
    run = subprocess.run([str(CatalogBuild.exe), 'pages', str(folder)], cwd=ROOT,
                         capture_output=True, check=True, timeout=40)
    rows = [json.loads(line) for line in run.stdout.splitlines()]
    collector = directory.CatalogCollector(host.DEVICE, host.BOOT, host.SESSION)
    for row in rows:
        collector.accept(row, request_id=row['request_id'], cursor=row['cursor'])
    if not collector.confirmed or collector.confirmed['package_sha256'] != packages.sha(raw):
        raise ValueError('flow_mounted_directory_incomplete')
    return rows, collector.confirmed


class Bridge:
    """Production ControlLink plus native scheduler; no serial worker exists."""
    def __init__(self, flash, folder, pages):
        self.flash, self.folder = flash, folder
        self.now, self.trace, self.consumed = 0, bytearray(), 0
        self.boot_char = 'bcdef0123456789a'[len(flash.boots) % 16]
        self.boot = 'boot-' + self.boot_char * 32
        self.link = control.ControlLink(nonce=lambda:'client-native', wall=lambda:'2026-11-09T00:00:00.000Z')
        self.link.observe(host.hello(self.boot_char), 0)
        self.stats, self.commands, self.pages = [], [], []
        self.replies, self.wires = [], []
        self.provider = folder / 'native-directory.txt'
        self.set_provider(pages)
        self.edition = flash.app_edition()
        self.generation = len(flash.boots)

    def set_provider(self, pages):
        head = pages[0]
        lines = [head[key] for key in ('catalog_id', 'package_sha256', 'index_sha256')]
        lines += [packages.canonical(row).decode('utf-8') for page in pages for row in page['entries']]
        self.total_entries = sum(len(page['entries']) for page in pages)
        self.provider.write_text('\n'.join(lines)+'\n', encoding='utf-8')

    def control(self):
        # A simulated boot is the transport disconnect boundary, just as USB
        # drops the live session. A new Bridge must open a new connection.
        if len(self.flash.boots) != self.generation or self.flash.app_edition() != self.edition:
            if self.link.enabled:
                self.link.disconnect(self.now, 'device_restarted')
        return self.link.snapshot(self.now)

    def snapshot(self):
        return dict(hardware_accessed=False)

    def close(self):
        pass

    def _native(self, steps):
        if (self.flash.app_edition() != self.edition
                or len(self.flash.boots) != self.generation):
            self.control()
            raise ValueError('flow_App_changed_reconnect_required')
        for code, now, *values in steps:
            self.trace.extend(code.encode() + struct.pack('<Q', now))
            if code == 'D':
                self.trace.extend(struct.pack('<I', len(values[0])) + values[0])
                self.wires.append(json.loads(values[0]))
            elif values:
                self.trace.append(values[0])
            self.now = max(self.now, now)
        path = self.folder / 'native-trace.bin'
        path.write_bytes(self.trace)
        run = subprocess.run([str(self.flash.executable()), str(self.provider), self.boot_char, str(path)], cwd=ROOT,
                             capture_output=True, check=True, timeout=20)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        for row in rows[self.consumed:]:
            self.replies.append(row)
            self.link.accept(row, self.now)
        self.consumed = len(rows)
        self.stats = list(map(int, run.stderr.split()))
        return rows

    def command(self, value):
        self.control()
        self.commands.append(deepcopy(value))
        self.link.command(value, self.now)
        return self.control()

    def request(self, operation, command_id):
        state = self.control()
        return dict(operation=operation, command_id=command_id,
            **{k:state[k] for k in ('connection_id','target_id','boot_id','session_id')})

    def open(self):
        start = self.now + 400 if self.trace else 0
        value = dict(operation='enable', command_id='flow-enable-'+str(start), connection_id=self.link.connection_id,
                     target_id=host.DEVICE, boot_id=self.boot, session_id=None)
        self.command(value)
        self._native([('D',start,self.link.tick(start))])
        self._native([('D',start+400,self.link.tick(start+400))])

    def disconnect(self):
        self._native([('X',self.now+1)])
        self.link.disconnect(self.now, 'connection_lost')

    def read_catalog(self, *, omit_last=False):
        self.command(self.request('read_catalog', 'catalog-'+str(self.now)))
        self.pages = []
        for number in range(64):
            self.now += 400
            if omit_last and self.control()['catalog'].get('next_cursor') == (self.total_entries-1)//2*2:
                self.link.tick(self.now)
                self.now += control.timing.REQUEST_TIMEOUT_MS + 1
                self.link.tick(self.now)
                break
            before = len(self.replies)
            self._native([('D',self.now,self.link.tick(self.now))])
            self.pages += self.replies[before:]
            if self.control()['catalog']['status'] != 'reading':
                break
        if self.control()['catalog']['status']=='complete':
            self.now += 400
            self._native([('D',self.now,self.link.tick(self.now))])
        return self.control()

    def dispatch(self, change=None):
        self.control()
        self.now += 400
        wire = self.link.tick(self.now)
        if not wire:
            return None
        self._native([('D',self.now,wire)])
        steps=[]
        if change=='menu':
            steps.append(('P',self.now+1,2))
        elif change=='voice':
            steps.append(('A',self.now+1,6))
        steps += [('U',self.now+10),('Z',self.now+20)]
        self._native(steps)
        return wire


class HTTP:
    def __init__(self, bridge, output):
        self.server = service.make_server(0, local=False, output=output, bridge=bridge)
        self.thread = threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, path, value):
        header = 'e2b' if path=='/api/package' else 'e3f2' if path.endswith('/overview') else 'e3e' if path.endswith('/resolve') else 'e3c6'
        raw = packages.canonical(value)
        connection = HTTPConnection('127.0.0.1',self.server.server_port,timeout=15)
        try:
            connection.request('POST',path,body=raw,headers={'Content-Type':'application/json','X-DeskBot-Studio':header})
            response=connection.getresponse()
            return response.status,json.loads(response.read())
        finally:
            connection.close()

    def download(self,path):
        connection = HTTPConnection('127.0.0.1',self.server.server_port,timeout=15)
        try:
            connection.request('GET',path)
            response=connection.getresponse()
            if response.status!=200:
                raise ValueError('flow_download_failed')
            return response.read()
        finally:
            connection.close()
