"""Browser fixture for the production overview, using memory instead of USB."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


service=load('e3f2_browser_service','scripts/serve-deskbot-studio-e3f2.py')
helpers=load('e3f2_browser_helpers','tests/serve_studio_e3e_fixture.py')


class Handler(service.StudioHandler):
    def do_GET(self):
        if self.path!='/__fixture/state':
            return super().do_GET()
        if self.allowed():
            self.json(200,dict(requests=self.server.bridge.requests,hardware_accessed=False))

    def do_POST(self):
        if self.path=='/__fixture/state':
            if not self.allowed():
                return
            size=int(self.headers.get('Content-Length','0'))
            if self.headers.get('X-DeskBot-Fixture')!='memory' or not 0<size<1024*1024:
                return self.json(400,dict(error='fixture_input'))
            value=json.loads(self.rfile.read(size))
            for key in ('live','body','fail_resolve'):
                if key in value:
                    setattr(self.server.bridge,key,value[key])
            return self.json(200,dict(ok=True))
        if self.path=='/api/role-expression/overview' and self.server.bridge.fail_resolve:
            return self.json(503,dict(error='本机分析暂未完成，请重试'))
        return super().do_POST()


if __name__=='__main__':
    server=service.make_server(0,local='--public' not in sys.argv,bridge=helpers.MemoryBridge())
    server.RequestHandlerClass=Handler
    print(f'Fixture E3f-2 http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
