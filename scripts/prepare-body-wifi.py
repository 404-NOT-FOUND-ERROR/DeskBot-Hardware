"""Create paired local profiles with separate credentials and a common device/network."""
import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("expression_profile", ROOT / "scripts/prepare-wifi-expression.py")
expression = importlib.util.module_from_spec(spec)
spec.loader.exec_module(expression)


def prepare(folder, source, network_settings=False):
    # The existing expression binding supplies public device/network metadata.
    config = json.loads(source.read_bytes())
    if config["schema"] != "deskbot.expression-wifi-config.v1":
        raise ValueError("expression_binding_required")
    if folder.exists():
        raise ValueError("fresh_profile_directory_required")
    expression.touch.prepare(folder / "touch", config["host"], 4318, lab=config["lab"])
    expression.prepare(folder / "expression", config["host"], 4319, config["device_id"],
        config["character_id"], config["graph_sha256"], config["role_id"], config["role_revision"], lab=config["lab"])
    paired = dict(schema="deskbot.body-wifi-profile.v1", host=config["host"], device_id=config["device_id"],
        touch_wifi_config="touch/receiver.json", expression_wifi_config="expression/receiver.json",
        edition="E3f-9d.7-network-settings" if network_settings else "E3f-9d.6-body-wifi", device_written=False)
    (folder / "profile.json").write_text(json.dumps(paired, indent=2) + "\n", encoding="utf-8")
    return dict(profile=str(folder), host=config["host"], device_id=config["device_id"], ready_for="offline_build")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--expression-binding", type=Path, required=True)
    parser.add_argument("--network-settings", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.profile.resolve(), args.expression_binding.resolve(), args.network_settings)))
