#!/usr/bin/env python3
"""E3b local server: USB device identity plus existing observation and packager."""
import argparse
import importlib.util
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from protocol.studio_identity_bridge import IdentityBridge

spec=importlib.util.spec_from_file_location('studio_e3b_service_parent',ROOT/'scripts/serve-deskbot-studio-e3a.py')
parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)


class StudioHandler(parent.StudioHandler):
    server_version='DeskBotStudio/3b'

    def do_GET(self):
        if urlsplit(self.path).path!='/api/health': return super().do_GET()
        if not self.allowed(): return
        state=self.server.bridge.snapshot()
        missing=[label for label,file in (('E1.18 基线资源',parent.parent.packages.BASE_IMAGE),
                  ('害羞候选',parent.parent.packages.CANDIDATE)) if not file.is_file()]
        self.json(200,{'service':'deskbot.studio.packager/v1','edition':'E3b','catalog_id':self.server.catalog['catalog_id'],
                       'target_id':self.server.target['id'],'ready':not missing,'missing':missing,
                       'hardware_accessed':state['hardware_accessed'],'device_written':False,
                       'observation':'usb_console','device_controls':False,'identity_status':state['identity']['status']})


class StudioServer(parent.StudioServer):
    def __init__(self,port=8766,*,local=True,output=None,serial_python=None,bridge=None):
        super().__init__(port,local=local,output=output,bridge=bridge or IdentityBridge(serial_python))
        self.RequestHandlerClass=StudioHandler
        self.page=ROOT/('tmp' if local else 'artifacts')/'deskbot-studio-e3b-20261104/index.html'
        if not self.page.is_file(): self.server_close();raise FileNotFoundError('请先生成 E3b 工作台')


make_server=StudioServer


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8766);parser.add_argument('--public',action='store_true')
    parser.add_argument('--serial-python',type=Path);args=parser.parse_args()
    server=make_server(args.port,local=not args.public,serial_python=args.serial_python)
    print(f'DeskBot Studio E3b: http://127.0.0.1:{server.server_port}/#connection',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
