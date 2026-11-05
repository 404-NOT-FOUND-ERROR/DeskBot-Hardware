#!/usr/bin/env python3
"""Summarize E3c deployment and real USB receipts from local evidence."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_app_deployment as tx
from protocol import studio_e3c_deployment as reviewed
from protocol.device_link import validate_hello

spec = importlib.util.spec_from_file_location("e3c_audit_deployer", ROOT / "scripts/deploy-deskbot-studio-e3c.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
verify_spec = importlib.util.spec_from_file_location("e3c_audit_live_checks", ROOT / "tools/verify-studio-control-e3c.py")
verify = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verify)
PUBLIC = ROOT / "artifacts/deskbot-studio-e3c7-20261105/live-deployment.json"
EDITION, ROLLBACK_TARGET = 'E3c-7', 'E3b-revision-2'


def audit(folder, *, evidence_name='control-evidence.json', query_only=False):
    plan = reviewed.check_plan(tx.load(folder))
    state = tx.Journal(folder, plan).load()
    install = json.loads((folder / "install-result.json").read_bytes())
    restart = json.loads((folder / "restart-result.json").read_bytes())
    if evidence_name != 'control-evidence.json' and not verify.re.fullmatch(r'control-evidence-[a-z0-9-]{1,32}\.json', evidence_name):
        raise ValueError('invalid control evidence filename')
    observed = json.loads((folder / evidence_name).read_bytes())
    if (state is None or state["phase"] != "installed" or install["result"] != "candidate_installed"
            or restart["result"] != "restart_verified" or not restart["boot_id_changed"]
            or not restart["device_id_stable"] or observed["plan_id"] != plan.id
            or observed["result"] != ("display_state_verified" if query_only else "neutral_receipts_and_reconnect_passed")
            or observed["expression_requests"] != (0 if query_only else 1)
            or (not query_only and not all(observed[key] for key in ("request_correlated", "repeated_host_command_did_not_reexecute",
                                                "old_session_cleared", "fresh_session_after_reconnect")))):
        raise ValueError("completed deployment, restart and one-request evidence required")
    first, second = install["boot"], restart["boot"]
    for boot in (first, second):
        validate_hello(boot["hello"])
        if not boot["periodic_identity_verified"] or boot["hello_count"] < 3:
            raise ValueError("periodic boot identity required")
    if (first["hello"]["device_id"] != second["hello"]["device_id"]
            or first["hello"]["boot_id"] == second["hello"]["boot_id"]):
        raise ValueError("restart identity differs")
    for key in (("first",) if query_only else ("first", "reconnected")):
        hello = validate_hello(observed[key]["identity"]["hello"])
        if any(hello[name] != second["hello"][name] for name in ("device_id", "boot_id", "firmware", "hardware")):
            raise ValueError("Studio identity differs from flashed App")
    state_keys = (("passive", "queried") if query_only else ("passive", "ready", "terminal", "after_duplicate", "disabled",
                "passive_after_reconnect", "ready_after_reconnect"))
    for key in state_keys:
        verify.checked_state(observed[key])
    if query_only:
        display = observed['queried']['display']
        readback = install['readback']
        if (display is None or observed['passive']['enabled'] or observed['passive']['frames_sent'] != 0
                or observed['queried']['age_ms'] is None or not 0 <= observed['queried']['age_ms'] < 2000
                or any(observed['queried'][key] != second['hello'][key] for key in ('boot_id',))
                or observed['queried']['target_id'] != second['hello']['device_id']
                or any(display[key] != observed['queried'][key] for key in ('target_id', 'boot_id', 'session_id'))
                or readback['app_sha256'] != tx.sha(plan.candidate)
                or not all(readback[key] for key in ('app_byte_equal', 'static_outside_app_equal', 'nvs_equal'))):
            raise ValueError('passive connection, display state and readback required')
        return {'schema': 'deskbot.studio_control_live/v1', 'edition': EDITION, 'date': '2026-11-05',
            'result': 'app_readback_restart_display_query_passed_expression_pending',
            'run_label': folder.name, 'plan_id': plan.id, 'device_written': True,
            'candidate': {'bytes': len(plan.image), 'sha256': tx.sha(plan.image),
                'app_elf_sha256': second['hello']['firmware']['app_elf_sha256'],
                'partition_headroom_bytes': tx.PARTITION - len(plan.image)},
            'write_scope': {'target': 'body_COM4', 'offset': tx.START, 'erase_bytes': len(plan.rollback),
                'end_exclusive': tx.START + len(plan.rollback)},
            'verification': readback, 'boot': {'initial_hello_count': first['hello_count'],
                'restart_hello_count': second['hello_count'], 'device_id_stable': True, 'boot_id_changed': True},
            'control': {'single_usb_owner': True, 'expression_requests': 0,
                'display': {key: display[key] for key in ('application', 'page', 'player', 'sleep', 'preview')},
                'reason': display['preview']['reason'], 'state_age_ms': observed['queried']['age_ms']},
            'backup': {'rollback_target': ROLLBACK_TARGET, 'flash_bytes': len(plan.snapshot),
                'retained_locally': True, 'accepted_E1_42_recovery_retained': True},
            'hardware_acceptance': 'pending', 'screen_playback_confirmed': False, 'base_tests': 'after_repair',
            'evidence_sha256': {name: tx.sha((folder / name).read_bytes()) for name in
                ('preflight.json', 'install-result.json', 'restart-result.json', evidence_name)}}
    terminal = observed["terminal"]
    receipts = terminal["receipts"]
    if ([row["status"] for row in receipts] != ["accepted", "dispatched"]
            or any(row["source"] != "device" or any(row[name] != terminal[name]
                   for name in ("target_id", "boot_id", "session_id")) for row in receipts)
            or receipts[0]["request_id"] != receipts[1]["request_id"]
            or observed["after_duplicate"]["receipts"] != receipts
            or observed["disabled"]["enabled"] or observed["disabled"]["session_id"] is not None
            or observed["ready"]["session_id"] == observed["ready_after_reconnect"]["session_id"]):
        raise ValueError("correlated receipts and rotated sessions required")
    readback = install["readback"]
    if (readback["app_sha256"] != tx.sha(plan.candidate) or not all(readback[key] for key in
            ("app_byte_equal", "static_outside_app_equal", "nvs_equal"))):
        raise ValueError("App and outside-App verification required")
    return {"schema": "deskbot.studio_control_live/v1", "edition": EDITION, "date": "2026-11-05",
        "result": "app_readback_state_neutral_receipts_reconnect_passed_interaction_pending",
        "run_label": folder.name, "plan_id": plan.id, "device_written": True,
        "candidate": {"bytes": len(plan.image), "sha256": tx.sha(plan.image),
                      "app_elf_sha256": second["hello"]["firmware"]["app_elf_sha256"],
                      "partition_headroom_bytes": tx.PARTITION - len(plan.image)},
        "write_scope": {"target": "body_COM4", "offset": tx.START, "erase_bytes": len(plan.rollback),
                        "end_exclusive": tx.START + len(plan.rollback)},
        "backup": {"flash_bytes": len(plan.snapshot), "sha256": tx.sha(plan.snapshot),
                   "rollback_target": ROLLBACK_TARGET, "original_bytes": reviewed.ORIGINAL_SIZE,
                   "original_sha256": reviewed.ORIGINAL_SHA, "rollback_bytes": len(plan.rollback),
                   "rollback_sha256": tx.sha(plan.rollback), "retained_locally": True,
                   "accepted_E1_42_recovery_retained": True},
        "verification": readback,
        "boot": {"initial_hello_count": first["hello_count"], "restart_hello_count": second["hello_count"],
                 "device_id_stable": True, "boot_id_changed": True},
        "control": {"single_usb_owner": True, "explicit_enable": True, "neutral_requests": 1,
                    "semantic": "neutral", "resource": "winking.eaf", "implicit_action": None,
                    "receipt_statuses": [row["status"] for row in receipts],
                    "receipt_reasons": [row["reason"] for row in receipts],
                    "repeated_host_command_did_not_reexecute": True,
                    "fresh_session_after_reconnect": True,
                    "ready_application": observed["ready"]["display"]["application"],
                    "ready_page": observed["ready"]["display"]["page"],
                    "ready_player": observed["ready"]["display"]["player"],
                    "sleep_state": "unknown", "receipt_evidence": "submitted_to_player_api"},
        "hardware_acceptance": "pending", "screen_playback_confirmed": False,
        "base_tests": "after_repair",
        "evidence_sha256": {name: tx.sha((folder / name).read_bytes()) for name in
            ("preflight.json", "install-result.json", "restart-result.json", evidence_name)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument('--profile', choices=('e3c7', 'e3c8'), default='e3c7')
    parser.add_argument('--evidence-name', default='control-evidence.json')
    parser.add_argument('--query-only', action='store_true')
    args = parser.parse_args()
    if args.profile == 'e3c8':
        from protocol import studio_e3c8_deployment as reviewed
        EDITION, ROLLBACK_TARGET = 'E3c-8', 'E3c-7'
        PUBLIC = ROOT / 'artifacts/deskbot-studio-e3c8-20261105/live-deployment.json'
    if args.query_only:
        PUBLIC = PUBLIC.with_name('connection-verification.json')
    raw = (json.dumps(audit(deploy.folder_for(args.run), evidence_name=args.evidence_name,
        query_only=args.query_only), ensure_ascii=False, indent=2) + "\n").encode()
    if args.check:
        if PUBLIC.read_bytes() != raw:
            raise ValueError("retained E3c live report differs")
    else:
        PUBLIC.parent.mkdir(parents=True, exist_ok=True)
        tx.write_new(PUBLIC, raw)
    print('E3c App, restart and display query verified; expression and physical acceptance pending.' if args.query_only else
          'E3c live App, state, neutral receipts and reconnect audit passed; physical acceptance pending.')
