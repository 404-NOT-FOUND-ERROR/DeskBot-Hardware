"""Run the production request parser, USB endpoint, scheduler adapter and core."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from protocol import expression_control as contract
from protocol import studio_usb_session as session

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'firmware/body/main'
BOARD = MAIN / 'boards/esp_vocat'
TARGET, BOOT = 'device-'+'a'*32, 'boot-'+'b'*32
FIRST = 'session-'+'b'*32+'-1'
CATALOG = '42a6d3c6b062ab63daf23a95128382909b63b06aa4d2416cb1da9464411b4fb5'


class ExpressionControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('e3c5_compiler', ROOT / 'scripts/test-expression-e1-26-cpp.py')
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        command, kind = helper.find_compiler() or (None, None)
        if not command: raise RuntimeError('C++17 compiler required')
        (ROOT / 'tmp').mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(prefix='e3c5-native-', dir=ROOT / 'tmp')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.folder = Path(cls.temp.name)
        files = {
            'sdkconfig.h': '#pragma once\n' + ''.join('#define CONFIG_DESKBOT_STUDIO_'+s+'_ENABLE 1\n' for s in (
                'RUNTIME_SNAPSHOT', 'STATE_QUERY', 'DISPLAY_QUERY', 'EXPRESSION_REQUEST')),
            'freertos/FreeRTOS.h': '#pragma once\n',
            'freertos/portmacro.h': '''#pragma once
#include <mutex>
struct portMUX_TYPE { std::mutex mutex; };
#define portMUX_INITIALIZER_UNLOCKED {}
inline void portENTER_CRITICAL(portMUX_TYPE* p) { p->mutex.lock(); }
inline void portEXIT_CRITICAL(portMUX_TYPE* p) { p->mutex.unlock(); }
''',
            'esp_timer.h': '#pragma once\n#include <cstdint>\nint64_t esp_timer_get_time();\n',
            'application.h': '''#pragma once
#include <atomic>
#include <functional>
#include <mutex>
#include <vector>
#include <cstdlib>
extern std::atomic<unsigned> scheduled_peak;
class Application {
    std::mutex mutex; std::vector<std::function<void()>> tasks;
public:
    static Application& GetInstance() { static Application app; return app; }
    void Schedule(std::function<void()> task) {
        std::lock_guard<std::mutex> guard(mutex); tasks.push_back(task);
        if (tasks.size() > 1) std::abort(); scheduled_peak = tasks.size();
    }
    void Run() {
        std::function<void()> task;
        { std::lock_guard<std::mutex> guard(mutex); if (tasks.empty()) return; task = tasks.front(); tasks.clear(); }
        task();
    }
};
''',
            'display/emote_display.h': '''#pragma once
#include <atomic>
#include <cstring>
#include <cstdlib>
extern std::atomic<unsigned> display_calls;
class Display { public: virtual ~Display() = default; };
namespace emote { class EmoteDisplay : public Display {
public:
    bool handle = true;
    void* GetEmoteHandle() const { return handle ? (void*)this : nullptr; }
    void SetEmotion(const char* emotion) { if (std::strcmp(emotion, "neutral")) std::abort(); ++display_calls; }
}; }
''',
            'board.h': '''#pragma once
#include "display/emote_display.h"
class Board { public: emote::EmoteDisplay display;
static Board& GetInstance() { static Board board; return board; }
Display* GetDisplay() { return &display; }
};
''',
            'mbedtls/sha256.h': '''#pragma once
#include <cstdint>
#include <cstddef>
#include <atomic>
extern std::atomic<int64_t> test_us;
extern std::atomic<uint32_t> hash_delay_ms;
// Host-only deterministic fingerprint shim. Firmware links real mbedTLS SHA-256.
inline int mbedtls_sha256(const unsigned char* data, size_t size, unsigned char* out, int) {
    test_us += static_cast<int64_t>(hash_delay_ms.load())*1000;
    for (unsigned lane=0; lane<8; ++lane) {
        uint32_t h=2166136261U+lane;
        for (size_t i=0;i<size;++i) h=(h^data[i])*16777619U;
        for(unsigned j=0;j<4;++j) out[lane*4+j]=static_cast<unsigned char>(h>>(j*8));
    } return 0;
}
''',
        }
        for name, text in files.items():
            path = cls.folder / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        cls.exe = cls.folder / 'expression.exe'
        result = subprocess.run(command + (['c++'] if kind == 'zig-c++' else []) + [
            '-std=c++17', '-Wall', '-Wextra', '-Werror', '-UNDEBUG', '-fno-exceptions', '-pthread',
            '-include', str(cls.folder / 'sdkconfig.h'), '-I', str(cls.folder), '-I', str(MAIN), '-I', str(BOARD),
            str(BOARD / 'studio_runtime_state.cc'), str(BOARD / 'studio_expression_adapter.cc'),
            str(ROOT / 'tests/cpp/studio_expression_control_test.cc'), '-o', str(cls.exe)],
            cwd=ROOT, capture_output=True, text=True, timeout=180)
        if result.returncode: raise RuntimeError(result.stdout + result.stderr)

    def request(self, **changes):
        return dict(schema=contract.SCHEMA, message_type='request', request_id='expr-1', target_id=TARGET,
                    boot_id=BOOT, session_id=FIRST, sequence=1, catalog_id=CATALOG, role_id='role-default',
                    issued_at='2026-11-05T10:20:30.000Z', expires_after_ms=3000,
                    command=dict(kind='play_expression', semantic_id='neutral')) | changes

    def wire(self, **changes): return (json.dumps(self.request(**changes), separators=(',', ':'))+'\n').encode()
    def opening(self, **changes):
        return session.encode_request(**(dict(operation='open', request_id='open-1', target_id=TARGET,
            boot_id=BOOT, client_nonce='client-a', session_id=None) | changes))

    def trace(self, *operations, open_session=True):
        raw = bytearray()
        if open_session: operations = (('D', 0, self.opening()),) + operations
        for op in operations:
            code, now, *values = op; raw.extend(code.encode()+struct.pack('<Q', now))
            if code == 'D': raw.extend(struct.pack('<I', len(values[0]))+values[0])
            elif code in ('C','L'): raw.extend(struct.pack('<I', values[0]))
            elif values: raw.append(values[0])
        file = self.folder / 'trace.bin'; file.write_bytes(raw)
        run = subprocess.run([str(self.exe), str(file)], capture_output=True, check=True, timeout=20)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        for row in rows:
            if row['message_type'] == 'receipt': contract.validate_receipt(row)
        return [r for r in rows if r['message_type']=='receipt'], list(map(int, run.stderr.split()))

    def decode(self, raw):
        file = self.folder / 'decode.json'; file.write_bytes(raw)
        return subprocess.run([str(self.exe), 'decode', str(file)], capture_output=True, timeout=20)

    def test_neutral_accepted_then_owner_dispatch(self):
        rows, stats = self.trace(('D', 500, self.wire()), ('U', 510), ('Z', 520))
        self.assertEqual([r['status'] for r in rows], ['accepted', 'dispatched'])
        self.assertEqual((stats[0], stats[1]), (1, 1))
        self.assertEqual(rows[-1]['reason'], 'submitted_to_display')

    def test_receiving_never_executes(self):
        rows, stats = self.trace(('D', 500, self.wire()))
        self.assertEqual((rows[-1]['status'], stats[0]), ('accepted', 0))

    def test_busy_single_reservation(self):
        rows, stats = self.trace(('D', 500, self.wire()), ('D', 800, self.wire(request_id='expr-2', sequence=2)))
        self.assertEqual([r['status'] for r in rows], ['accepted', 'busy']); self.assertEqual(stats[1], 1)

    def test_busy_while_speaking(self):
        rows, stats = self.trace(('A', 400, 6), ('D', 500, self.wire()))
        self.assertEqual(rows[-1]['status'], 'busy'); self.assertEqual(stats[0], 0)

    def test_menu_rejected(self):
        rows, _ = self.trace(('P', 400, 2), ('D', 500, self.wire()))
        self.assertEqual(rows[-1]['reason'], 'not_home')

    def test_player_unavailable(self):
        rows, _ = self.trace(('R', 400, 1), ('D', 500, self.wire()))
        self.assertEqual(rows[-1]['reason'], 'player_unavailable')

    def test_owner_changes_before_dispatch(self):
        rows, stats = self.trace(('D', 500, self.wire()), ('P', 510, 2), ('U', 520), ('Z', 530))
        self.assertEqual(rows[-1]['status'], 'interrupted'); self.assertEqual(stats[0], 0)

    def test_ttl_boundary(self):
        rows, stats = self.trace(('D', 500, self.wire(expires_after_ms=100)), ('U', 600), ('Z', 610))
        self.assertEqual(rows[-1]['status'], 'expired'); self.assertEqual(stats[0], 0)

    def test_tick_expiry_keeps_old_callback_reserved(self):
        rows, stats = self.trace(('D', 500, self.wire(expires_after_ms=100)), ('T', 700), ('Z', 710),
            ('D', 800, self.wire(request_id='expr-2', sequence=2)), ('U', 810))
        self.assertEqual([r['status'] for r in rows], ['accepted', 'expired', 'busy']); self.assertEqual(stats[0], 0)

    def test_disconnect(self):
        rows, stats = self.trace(('D', 500, self.wire()), ('X', 510), ('U', 520), ('Z', 530))
        self.assertEqual(rows[-1]['status'], 'interrupted'); self.assertEqual(stats[0], 0)

    def test_lease_expiry(self):
        rows, stats = self.trace(('D', 29500, self.wire()), ('T', 30000), ('U', 30010), ('Z', 30020))
        self.assertEqual(rows[-1]['status'], 'interrupted'); self.assertEqual(stats[0], 0)

    def test_new_session_invalidates_old_callback(self):
        second = 'session-'+'b'*32+'-2'
        rows, stats = self.trace(('D', 500, self.wire()), ('D', 800, self.opening(client_nonce='client-b')),
            ('Z', 810), ('U', 820), ('D', 1100, self.wire(session_id=second)), ('U', 1110), ('Z', 1120))
        self.assertEqual([r['status'] for r in rows], ['accepted','interrupted','accepted','dispatched'])
        self.assertEqual(stats[0], 1)

    def test_duplicate_pending(self):
        rows, _ = self.trace(('D', 500, self.wire()), ('D', 800, self.wire()))
        self.assertEqual((rows[-1]['status'], rows[-1]['original_status']), ('duplicate','accepted'))

    def test_duplicate_completed(self):
        rows, stats = self.trace(('D', 500, self.wire()), ('U', 510), ('Z', 520), ('D', 800, self.wire()))
        self.assertEqual((rows[-1]['status'], rows[-1]['original_status']), ('duplicate','dispatched'))
        self.assertEqual(stats[0], 1)

    def test_conflicting_request_id(self):
        rows, _ = self.trace(('D', 500, self.wire()), ('D', 800, self.wire(expires_after_ms=500)))
        self.assertEqual(rows[-1]['reason'], 'request_id_conflict')

    def test_sequence_high_water(self):
        rows, _ = self.trace(('D', 500, self.wire()), ('D', 800, self.wire(request_id='expr-2')))
        self.assertEqual(rows[-1]['reason'], 'sequence_not_increasing')

    def test_ledger_rotation_and_old_replay(self):
        ops = [('P', 100, 2)]
        ops += [('D', 500+i*300, self.wire(request_id=f'expr-{i+1}', sequence=i+1)) for i in range(70)]
        ops += [('D', 22000, self.wire())]
        rows, stats = self.trace(*ops)
        self.assertEqual(rows[-1]['reason'], 'sequence_not_increasing'); self.assertLessEqual(stats[2], 12000)

    def test_pending_entry_survives_rotation(self):
        ops = [('D', 500, self.wire(expires_after_ms=10000))]
        ops += [('D', 800+i*251, self.wire(request_id=f'expr-{i+2}', sequence=i+2)) for i in range(32)]
        ops += [('D', 9000, self.wire(expires_after_ms=10000))]
        rows, _ = self.trace(*ops); self.assertEqual(rows[-1]['status'], 'duplicate')

    def test_full_ledger_rotation_and_stale_ticket(self):
        subprocess.run([str(self.exe),'core'],check=True,timeout=20)

    def test_parse_time_counts_against_ttl(self):
        rows, stats = self.trace(('L',400,200),('D',500,self.wire(expires_after_ms=100)))
        self.assertEqual(rows[-1]['status'],'expired'); self.assertEqual(stats[0],0)

    def test_deadline_exceeds_safe_counter(self):
        maximum = 2**53-1
        rows, _ = self.trace(('D',maximum-500,self.opening()),('D',maximum-200,self.wire()),open_session=False)
        self.assertEqual(rows[-1]['reason'],'deadline_out_of_range')

    def test_reversed_clock_cancels_owner_callback(self):
        rows, stats = self.trace(('D',500,self.wire()),('U',400),('Z',600))
        self.assertEqual(rows[-1]['status'],'interrupted'); self.assertEqual(stats[0],0)

    def test_utf8_surrogate_and_trailing_data_rejected(self):
        for data in [self.wire().strip()+b'{}',self.wire().replace(b'neutral',b'\\ud800'),
                     self.wire().replace(b'neutral','平静'.encode()),self.wire()+b'\xff']:
            self.assertNotEqual(self.decode(data).returncode,0)

    def test_identifiers_at_max_length_fit_receipt(self):
        rows, _ = self.trace(('D',500,self.wire(request_id='r'*96)),('U',510),('Z',520))
        self.assertEqual(rows[-1]['request_id'],'r'*96)
        self.assertLess(len(json.dumps(rows[-1],separators=(',',':')).encode()),1024)

    def test_wrong_target_boot_session_catalog_role(self):
        for field, value, reason in [('target_id','device-wrong','target_mismatch'), ('boot_id','boot-wrong','boot_changed'),
            ('session_id','session-wrong','session_changed'), ('catalog_id','c'*64,'catalog_or_role_changed'),
            ('role_id','role-other','catalog_or_role_changed')]:
            with self.subTest(field=field):
                rows, stats = self.trace(('D',500,self.wire(**{field:value})))
                self.assertEqual(rows[-1]['reason'],reason); self.assertEqual(stats[0],0)

    def test_unsupported_expression_and_action(self):
        for command in [dict(kind='play_expression',semantic_id='happy'), dict(kind='play_action',action_id='look-around')]:
            rows, stats = self.trace(('D',500,self.wire(command=command)))
            self.assertEqual(rows[-1]['reason'],'unsupported_expression_or_action'); self.assertEqual(stats[0],0)

    def test_terminal_mailbox_backpressure(self):
        rows, stats = self.trace(('D',500,self.wire()), ('U',510),
            ('D',800,self.wire(request_id='expr-2',sequence=2)), ('Z',810))
        self.assertEqual([r['status'] for r in rows],['accepted','busy','dispatched'])
        self.assertEqual([r['sequence'] for r in rows], [1,2,3]); self.assertEqual(stats[0],1)

    def test_no_live_handle(self):
        rows, stats = self.trace(('D',500,self.wire()), ('H',510,0), ('U',520), ('Z',530))
        self.assertEqual(rows[-1]['reason'],'player_unavailable'); self.assertEqual(stats[0],0)

    def test_small_output_does_not_consume_mailbox(self):
        rows, _ = self.trace(('D',500,self.wire()), ('U',510), ('C',520,100), ('Z',530), ('C',540,1024), ('Z',550))
        self.assertEqual(rows[-1]['status'],'dispatched')

    def test_no_session(self):
        rows, stats = self.trace(('D',500,self.wire()), open_session=False)
        self.assertEqual(rows, []); self.assertEqual(stats[0],0)

    def test_split_line_and_crlf(self):
        data = self.wire().rstrip(b'\n')+b'\r\n'
        rows, _ = self.trace(('D',500,data[:80]), ('D',600,data[80:]), ('U',610), ('Z',620))
        self.assertEqual([r['status'] for r in rows], ['accepted','dispatched'])

    def test_oversize_recovers(self):
        rows, _ = self.trace(('D',300,b'x'*2100+b'\n'), ('D',600,self.wire()))
        self.assertEqual(rows[-1]['status'],'accepted')

    def test_malformed_envelopes_are_silent(self):
        for data in [b'{}\n',self.wire()[:-2]+b',"extra":1}\n', self.wire().replace(b'"sequence":1',b'"sequence":1.0')]:
            rows, stats = self.trace(('D',500,data)); self.assertEqual(rows,[]); self.assertEqual(stats[0],0)

    def test_parser_material_matches_decoded_fields(self):
        request = self.request(); data = self.decode(self.wire().strip())
        fields = [request[k] for k in ['request_id','target_id','boot_id','session_id','catalog_id','role_id','issued_at']]
        fields += ['1','3000','expression','neutral']
        self.assertEqual(data.returncode,0); self.assertEqual(data.stdout,('\0'.join(fields)+'\0').encode())

    def test_json_order_and_ascii_unicode_escape(self):
        data = self.wire().strip().replace(b'neutral',b'neutr\\u0061l')
        self.assertEqual(self.decode(data).stdout,self.decode(self.wire().strip()).stdout)
        request = self.request(); reordered = dict(reversed(list(request.items())))
        self.assertEqual(self.decode(json.dumps(reordered).encode()).stdout,self.decode(self.wire().strip()).stdout)

    def test_all_fields_required(self):
        for key in self.request():
            value = self.request(); del value[key]
            self.assertNotEqual(self.decode(json.dumps(value).encode()).returncode,0,key)

    def test_duplicate_fields_rejected(self):
        for data in [self.wire().strip()[:-1]+b',"sequence":2}', self.wire().strip().replace(b'"kind":"play_expression"',b'"kind":"play_expression","kind":"play_expression"')]:
            self.assertNotEqual(self.decode(data).returncode,0)

    def test_invalid_dates_numbers_ids(self):
        cases = [dict(issued_at='2025-02-29T00:00:00.000Z'),dict(issued_at='2026-11-05T24:00:00.000Z'),
                 dict(sequence=0),dict(sequence=2**53),dict(sequence=True),dict(expires_after_ms=99),
                 dict(expires_after_ms=10001),dict(request_id='1bad'),dict(request_id='a'*97),dict(catalog_id='G'*64)]
        for changes in cases: self.assertNotEqual(self.decode(self.wire(**changes).strip()).returncode,0,changes)

    def test_valid_leap_day_and_max_integer(self):
        self.assertEqual(self.decode(self.wire(issued_at='2024-02-29T23:59:59.999Z',sequence=2**53-1).strip()).returncode,0)

    def test_command_shape_rejected(self):
        for command in [dict(kind='play_action',semantic_id='neutral'),dict(kind='play_expression',action_id='neutral'),
                        dict(kind='play_expression',semantic_id='neutral',extra=1)]:
            self.assertNotEqual(self.decode(self.wire(command=command).strip()).returncode,0)

    def test_concurrent_snapshot_writers(self):
        subprocess.run([str(self.exe),'race'],check=True,timeout=30)

    def test_default_off_and_owner_scope(self):
        text = (MAIN/'Kconfig.projbuild').read_text(encoding='utf-8').split('config DESKBOT_STUDIO_EXPRESSION_REQUEST_ENABLE',1)[1].split('\nconfig ',1)[0]
        self.assertIn('default n',text); self.assertIn('depends on DESKBOT_STUDIO_DISPLAY_QUERY_ENABLE',text)
        adapter = (BOARD/'studio_expression_adapter.cc').read_text(encoding='utf-8')
        self.assertEqual(adapter.count('SetEmotion("neutral")'),1)
        for token in ['xTaskCreate(', 'lv_obj', 'vocat_base_control', 'Nvs', 'insert.eaf']:
            self.assertNotIn(token,adapter)
        old = json.loads((ROOT/'artifacts/deskbot-studio-e3c4-20261105/firmware-build-report.json').read_bytes())['source_sha256']
        import hashlib
        for name in ['main/application.cc','main/boards/esp_vocat/ui_bridge.cc','main/display/emote_display.cc']:
            self.assertEqual(hashlib.sha256((MAIN.parent/name).read_bytes()).hexdigest(),old[name])


if __name__ == '__main__': unittest.main()
