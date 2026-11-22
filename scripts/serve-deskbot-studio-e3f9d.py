"""Serve device roles through the existing USB owner and two-role workbench."""
import argparse
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('device_roles_server_parent',ROOT/'scripts/serve-deskbot-studio-e3f9b.py')
parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)


class Handler(parent.Handler):
    server_version='DeskBotStudio/3f9d'
    def json(self,code,value):
        if isinstance(value,dict) and 'edition' in value: value=dict(value,edition='E3f-9d')
        return super().json(code,value)


class StudioServer(parent.StudioServer):
    def __init__(self,port=8779,*,local=True,**kwargs):
        super().__init__(port,local=local,**kwargs)
        self.page=ROOT/('tmp' if local else 'artifacts')/'deskbot-studio-e3f9d-20261112/index.html'
        if not self.page.is_file():
            self.server_close();raise FileNotFoundError('请先生成 E3f-9d 工作台')
        self.role_media={name:(self.page.parent/name,sha) for name,(_,sha) in self.role_media.items()}
        self.RequestHandlerClass=Handler


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8779);parser.add_argument('--serial-python',type=Path)
    parser.add_argument('--state-path',type=Path,default=parent.STATE_PATH)
    parser.add_argument('--package-output',type=Path)
    args=parser.parse_args();server=StudioServer(args.port,serial_python=args.serial_python,state_path=args.state_path,package_output=args.package_output)
    print(f'DeskBot Studio E3f-9d: http://127.0.0.1:{server.server_port}/#roles',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
