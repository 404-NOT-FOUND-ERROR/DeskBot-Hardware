#!/usr/bin/env python3
"""Serve click-then-check controls with the existing unique serial owner."""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ready_server_parent', ROOT / 'scripts/serve-deskbot-studio-e3f4a.py')
parent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parent)


class Handler(parent.Handler):
    server_version = 'DeskBotStudio/3f4b'

    def json(self, code, value):
        if isinstance(value, dict) and value.get('edition') == 'E3f-2':
            value = dict(value, edition='E3f-4b')
        return super().json(code, value)


class StudioServer(parent.StudioServer):
    def __init__(self, port=8775, **kwargs):
        super().__init__(port, **kwargs)
        self.RequestHandlerClass = Handler
        self.page = ROOT / 'tmp/deskbot-studio-e3f4b-20261109/index.html'
        if not self.page.is_file():
            self.server_close()
            raise FileNotFoundError('请先生成 E3f-4b 工作台')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8775)
    parser.add_argument('--serial-python', type=Path)
    args = parser.parse_args()
    server = StudioServer(args.port, local=True, serial_python=args.serial_python)
    print(f'DeskBot Studio E3f-4b: http://127.0.0.1:{server.server_port}/#connection', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
