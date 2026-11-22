#!/usr/bin/env python3
"""Role nodes with the reviewed asset layouts over the existing USB owner."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol.studio_role_layout import load_requirements
from protocol.studio_installed_catalog import validate_manifest

spec = importlib.util.spec_from_file_location("node_layout_parent", ROOT / "scripts/serve-deskbot-studio-e3f4b.py")
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class Handler(parent.Handler):
    server_version = "DeskBotStudio/3f6"

    def json(self, code, value):
        if isinstance(value, dict) and value.get("edition") == "E3f-2":
            value = dict(value, edition="E3f-6", asset_layouts=True)
        return super().json(code, value)


class StudioServer(parent.StudioServer):
    def __init__(self, port=8776, *, local=True, **kwargs):
        super().__init__(port, local=local, **kwargs)
        try:
            self.layout_requirements = load_requirements(self.catalog)
            self.reference = validate_manifest(json.loads(
                (ROOT / "artifacts/studio-resources-e3f4d/expected-directory.json").read_bytes()))
            self.RequestHandlerClass = Handler
            self.page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e3f6-20261110/index.html"
            if not self.page.is_file():
                raise FileNotFoundError("请先生成 E3f-6 工作台")
        except Exception:
            self.server_close()
            raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--serial-python", type=Path)
    args = parser.parse_args()
    server = StudioServer(args.port, serial_python=args.serial_python)
    print(f"DeskBot Studio E3f-6: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
