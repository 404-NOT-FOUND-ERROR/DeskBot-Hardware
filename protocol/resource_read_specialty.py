"""Run the unchanged mapping probe with an exact accepted-catalog rollback."""
from __future__ import annotations

import json
from pathlib import Path

from protocol import studio_app_deployment as tx
from protocol import studio_e3d2b_deployment as catalog
from protocol import storage_continuous_mmap_e3c3b_baseline as probe
from protocol.storage_continuous_mmap import reference_for, extract_records, validate_capture

ROOT = Path(__file__).resolve().parents[1]
INSTALLED_RUN = Path("tmp/e3c-live-20261108-e3d2b-live1")
INSTALLED_PLAN_SHA = "e2cf4540f037b4a9f25d5bfc9742d9ce6a0ca172322a91bcde76c4dbe0e853fd"
INSTALLED_PLAN_ID = "7f0e990ff8e3bb3ca7cf57fe268e53766c08bd2e86f1733a0c9afcf8eced7a6f"
INSTALLED_APP_UNION_SHA = "a461a4d431296b35a04150c267caba3ffde700fc56a40c27d3560edde35d321f"
ARCHIVE = Path("artifacts/studio-catalog-e3d2b-20261108")
LIVE_SHA = "4a97551b9aee55a028c91e4d6aba0437858abbbfd283ba142903d50e33a57dba"
ACCEPTANCE_SHA = "b3374745e24f59b68a6699616eaaa7a50ac537e819f89dfa082c60ef328c0e8b"
SPAN = 0x450000


def _fixed(root, path, digest):
    raw = probe.repo_bytes(root, path)
    if tx.sha(raw) != digest:
        raise ValueError("fixed resource-test evidence changed: " + str(path))
    return json.loads(raw)


def load_inputs(root=ROOT):
    """Reconstruct the installed bytes; refresh NVS at the device boundary."""
    _fixed(root, INSTALLED_RUN / "plan.json", INSTALLED_PLAN_SHA)
    live = _fixed(root, ARCHIVE / "live-result.json", LIVE_SHA)
    accepted = _fixed(root, ARCHIVE / "manual-acceptance-followup.json", ACCEPTANCE_SHA)
    old = tx.load(probe.safe_path(root / INSTALLED_RUN, root))
    state = tx.Journal(root / INSTALLED_RUN, old).load()
    if (old.id != INSTALLED_PLAN_ID or state is None or state.get("phase") != "installed"
            or old.metadata["identity_sha256"] != catalog.IDENTITY_SHA
            or live.get("plan_id") != old.id
            or accepted.get("plan_id") != old.id or accepted.get("result") != "passed"
            or accepted.get("app_sha256") != catalog.APP_SHA
            or accepted.get("app_elf_sha256") != catalog.APP_ELF_SHA
            or not accepted.get("items") or not all(v is True for v in accepted["items"].values())
            or tx.sha(old.image) != catalog.APP_SHA
            or live["readback"]["app_sha256"] != tx.sha(old.candidate)):
        raise ValueError("accepted installed E3d-2b required")
    current = catalog.reviewed_candidate(old.image, root)
    end = tx.START + len(old.rollback)
    snapshot = old.snapshot[:tx.START] + old.candidate + old.snapshot[end:]
    built = probe.load_e3c3b_probe_inputs(root)
    return dict(snapshot=snapshot, image=built["candidate"], identity_sha256=catalog.IDENTITY_SHA,
                original=current, source=dict(plan_id=old.id, live_sha256=LIVE_SHA,
                acceptance_sha256=ACCEPTANCE_SHA, probe_sha256=tx.sha(built["candidate"])))


def check_current(raw, expected):
    # Runtime configuration can change NVS. Every other byte belongs to the
    # accepted installed snapshot, including the complete upper resource area.
    if (len(raw) != tx.FLASH or len(expected) != tx.FLASH
            or raw[:0x9000] != expected[:0x9000] or raw[0xD000:] != expected[0xD000:]):
        raise ValueError("device Flash differs from installed E3d-2b")


def validate_plan(plan, *, candidate=True):
    plan.validate()
    # Pin the entire erase union, including bytes after the executable image.
    # Recomputed plan metadata cannot substitute different restoration bytes.
    if (plan.metadata["identity_sha256"] != catalog.IDENTITY_SHA
            or plan.metadata["original_bytes"] != catalog.APP_SIZE
            or len(plan.rollback) != SPAN
            or tx.sha(plan.rollback) != INSTALLED_APP_UNION_SHA
            or tx.sha(plan.rollback[:catalog.APP_SIZE]) != catalog.APP_SHA):
        raise ValueError("resource test rollback must be accepted E3d-2b")
    if candidate and (not isinstance(plan, tx.Plan) or len(plan.image) != probe.PROBE_APP_BYTES
                      or tx.sha(plan.image) != probe.PROBE_APP_SHA256):
        raise ValueError("resource test requires the unchanged E3c-3b probe")
    return plan


def prepare(folder, device_factory, *, inputs=None):
    inputs = load_inputs() if inputs is None else inputs
    folder.mkdir()
    device = device_factory(folder, inputs["identity_sha256"], None)
    confirmed = False
    try:
        if device.identity() != inputs["identity_sha256"]:
            raise ValueError("resource-test device identity changed")
        confirmed = True
        fresh = device.read_reference(inputs)
        check_current(fresh, inputs["snapshot"])
        plan = validate_plan(tx.prepare(fresh, inputs["image"], inputs["identity_sha256"],
                                         catalog.APP_SIZE, edition="E3c"))
        tx.persist(folder, plan)
        tx.write_new(folder / "preflight.json", tx.canonical(dict(
            schema="deskbot.resource_read_preflight/v1", result="prepared", plan_id=plan.id,
            source=inputs["source"], backup_method="fresh_prefix_and_full_device_digest",
            original_app="E3d-2b", probe_app="E3c-3b-mapping", device_written=False)))
        # Successful prepare leaves the chip in ROM for immediate execution.
        # On preparation failure, boot cleanup restores its running state.
        return dict(result="prepared", plan_id=plan.id, erase_bytes=len(plan.rollback))
    except BaseException:
        if confirmed:
            device.boot(False)
        raise


def check(folder, *, inputs=None):
    inputs = load_inputs() if inputs is None else inputs
    plan = validate_plan(tx.load(folder))
    receipt = json.loads((folder / "preflight.json").read_bytes())
    if (receipt.get("schema") != "deskbot.resource_read_preflight/v1"
            or receipt.get("source") != inputs["source"] or receipt.get("plan_id") != plan.id
            or receipt.get("result") != "prepared" or receipt.get("device_written") is not False):
        raise ValueError("resource-test preflight differs")
    check_current(plan.snapshot, inputs["snapshot"])
    return plan


def execute(folder, confirm_plan, device_factory, *, inputs=None):
    plan = check(folder, inputs=inputs)
    if confirm_plan != plan.id:
        raise ValueError("resource-test plan ID differs")
    journal = tx.Journal(folder, plan)
    if journal.load() is not None or (folder / "execute-result.json").exists():
        raise FileExistsError("execution already started; inspect or recover this run")
    device = device_factory(folder, plan.metadata["identity_sha256"], plan)
    outcome = dict(schema="deskbot.resource_read_result/v1", plan_id=plan.id,
                   result="started", runtime=None, restored=False, interaction="pending")
    try:
        installed = tx.install(plan, device, journal)
        log = device.probe_log
        ref = reference_for(plan.snapshot[0x1000000:0x1800000],
              identity_sha256=plan.metadata["identity_sha256"], run_label=folder.name,
              source_sha256=tx.sha(plan.snapshot))
        runtime = validate_capture(extract_records(log), ref)
        outcome.update(runtime=runtime, installation=installed)
        if runtime["probe_passed"] is not True:
            raise ValueError("mapping diagnostic did not pass")
        outcome["result"] = "automatic_mapping_passed"
    except BaseException as error:
        outcome.update(result="failed", error=type(error).__name__ + ": " + str(error))
        raise
    finally:
        state = journal.load()
        try:
            if state is not None and state["phase"] in tx.RECOVERY_PHASES and state["phase"] != "restored":
                outcome["recovery"] = tx.recover(plan, device, journal)
            elif state is None or state["phase"] == "prepared":
                outcome["boot_cleanup"] = device.boot(False)
            state = journal.load()
            outcome["restored"] = state is not None and state["phase"] == "restored"
        except BaseException as error:
            outcome.update(result="needs_recovery", recovery_error=type(error).__name__ + ": " + str(error))
            raise
        finally:
            tx.write_new(folder / "execute-result.json", tx.canonical(outcome))
    return outcome


def recover(folder, device_factory):
    # Recovery deliberately loads no candidate, acceptance or build report.
    plan = validate_plan(tx.load_recovery(folder), candidate=False)
    device = device_factory(folder, plan.metadata["identity_sha256"], plan)
    result = tx.recover(plan, device, tx.Journal(folder, plan))
    number = 1
    while (folder / f"recovery-result-{number:03d}.json").exists():
        number += 1
    tx.write_new(folder / f"recovery-result-{number:03d}.json", tx.canonical(result))
    return result
