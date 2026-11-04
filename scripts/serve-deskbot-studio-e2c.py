#!/usr/bin/env python3
"""Serve E2c using the tested E2b loopback package service."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def server_module():
    spec = importlib.util.spec_from_file_location("studio_e2b_service", ROOT / "scripts/serve-deskbot-studio-e2b.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_server(port=8766, *, local=True, output=None):
    module = server_module()
    server = module.StudioServer(port, **({"output": output} if output else {}), local=local)
    page = ROOT / ("tmp" if local else "artifacts") / "deskbot-studio-e2c-20261104/index.html"
    if not page.is_file():
        server.server_close()
        raise FileNotFoundError("Build the E2c workbench before starting its service")
    server.page = page
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--public", action="store_true")
    args = parser.parse_args()
    server = make_server(args.port, local=not args.public)
    print(f"DeskBot Studio E2c: http://127.0.0.1:{server.server_port}/#connection", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
