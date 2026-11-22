"""Create a local HTTPS expression profile, printing public paths only."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("touch_profile", ROOT / "scripts/prepare-head-touch-wifi.py")
touch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(touch)


def prepare(folder, host, port, device, character, graph, role, revision=None, *, lab=False):
    identifier = r"[a-zA-Z][a-zA-Z0-9_.:-]{0,63}"
    if (not re.fullmatch(r"device-[a-f0-9]{32}", device)
        or not re.fullmatch(identifier, character) or not re.fullmatch(identifier, role)
        or not re.fullmatch(r"[a-f0-9]{64}", graph)
        or revision is not None and (type(revision) is not int or not 0 <= revision <= 9007199254740991)):
        raise ValueError("invalid_expression_binding")
    result = touch.prepare(folder, host, port, lab=lab)
    path = folder / "receiver.json"
    config = json.loads(path.read_bytes())
    config.update(schema="deskbot.expression-wifi-config.v1", device_id=device, character_id=character,
                  graph_sha256=graph, role_id=role, role_revision=revision)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    previous = folder / "head_touch_wifi.local.h"
    header = previous.read_text(encoding="utf-8").replace("kTouch", "kExpression").replace(
        "/api/hardware/head-touch", "/api/hardware/expression/exchange")
    (folder / "wifi_expression.local.h").write_text(header, encoding="utf-8")
    previous.unlink()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=4319)
    parser.add_argument("--device", required=True)
    parser.add_argument("--character", required=True)
    parser.add_argument("--graph", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--role-revision", type=int)
    parser.add_argument("--lab", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.profile.resolve(), args.host, args.port, args.device, args.character,
                             args.graph, args.role, args.role_revision, lab=args.lab)))
