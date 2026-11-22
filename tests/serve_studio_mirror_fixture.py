"""Real local packaging with test-owned output and role state."""
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('mirror_http_fixture',ROOT/'scripts/serve-deskbot-studio-e3f8.py')
service=importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)
folder=Path(sys.argv[1])
server=service.StudioServer(0,state_path=folder/'state.json',output=folder/'packages')
print(f'http://127.0.0.1:{server.server_port}',flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
