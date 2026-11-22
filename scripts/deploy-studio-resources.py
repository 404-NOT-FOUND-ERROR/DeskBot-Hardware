#!/usr/bin/env python3
"""Prepare/check/install/recover E1.45 resources. Defaults to offline checking."""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_resource_installation as resources

CANDIDATE = ROOT / "tmp/expression-e1-45-staging-20261104/candidate-assets-8m.bin"


def run(phase, folder, *, snapshot=None, identity=None, confirm_plan=None, device_factory=None,
        baseline="E3c-3b"):
    if not isinstance(baseline, str) or baseline not in resources.BASELINES:
        raise ValueError("unsupported_resource_baseline")
    folder = resources._safe(Path(folder), ".").absolute()
    if folder.parent != ROOT / "tmp" or re.fullmatch(r"e145-assets-[0-9]{8}-[a-z0-9-]{1,30}", folder.name) is None:
        raise ValueError("resource_run_must_be_named_child_of_tmp")
    if phase in {"prepare-offline", "prepare-live"}:
        if folder.exists():
            raise FileExistsError("choose_new_resource_run_name")
        image = CANDIDATE.read_bytes()
        if resources.sha(image) != resources.CANDIDATE_SHA:
            raise ValueError("candidate_resource_changed")
        folder.mkdir()
        if phase == "prepare-offline":
            raw = snapshot if type(snapshot) is bytes else Path(snapshot).read_bytes()
            plan = resources.prepare(raw, image, identity, mode="offline", baseline=baseline)
        else:
            if device_factory is None:
                device_factory = transport_for(baseline)
            device = device_factory(folder, expected_identity=identity)
            confirmed = False
            ready = False
            try:
                actual_identity = device.identity()
                confirmed = True
                plan = resources.prepare(device.read_full(), image, actual_identity, mode="live", baseline=baseline)
                resources.persist(folder, plan)
                ready = True
            finally:
                # E3f-4c continues directly into install. Running the App here
                # could update NVS and invalidate the just-saved snapshot.
                needs_boot = baseline != "E3f-4c" or confirmed and not ready
                if needs_boot and not device.boot():
                    raise ValueError("preparation_original_boot_incomplete")
            result = resources.check(plan)
            result.update(device_accessed=True, download_mode_retained=baseline == "E3f-4c")
            return result
        resources.persist(folder, plan)
        return resources.check(plan)
    plan = resources.load(folder, recovery=phase == "recover")
    if resources.plan_baseline(plan) != baseline:
        raise ValueError("resource_entry_baseline_changed")
    if phase == "check":
        return resources.check(plan)
    if phase not in {"install", "recover"}:
        raise ValueError("unknown_resource_phase")
    if plan.metadata["mode"] != "live" or confirm_plan != plan.id:
        raise ValueError("matching_live_resource_plan_id_required")
    if device_factory is None:
        device_factory = transport_for(baseline)
    device = device_factory(folder, plan=plan, write_enabled=True)
    journal = resources.Journal(folder, plan)
    return (resources.install if phase == "install" else resources.recover)(plan, device, journal)


def transport_for(baseline):
    # Offline preparation and checks do not import the serial/build runtime.
    if baseline == "E3d-2b":
        from protocol.studio_resource_e3d2b_esptool import ResourceDevice
    elif baseline == "E3f-4c":
        from protocol.studio_resource_e3f4c_esptool import ResourceDevice
    elif baseline == "E3c-3b":
        from protocol.studio_resource_esptool import ResourceDevice
    else:
        raise ValueError("unsupported_resource_baseline")
    return ResourceDevice


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare-offline", "prepare-live", "check", "install", "recover"))
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--identity")
    parser.add_argument("--confirm-plan")
    args = parser.parse_args()
    result = run(args.phase, ROOT / "tmp" / args.run_label, snapshot=args.snapshot,
                 identity=args.identity, confirm_plan=args.confirm_plan)
    print(json.dumps(result, ensure_ascii=False))
