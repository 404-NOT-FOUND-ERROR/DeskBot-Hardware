"""Compile and run the production DFM parser, with a source-keyed executable cache."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from protocol.layered_face import ROOT


def validate(path):
    board=ROOT/'firmware/body/main/boards/esp_vocat'
    sources=[board/'studio_layered_face.h',board/'studio_layered_face.inc',ROOT/'tests/cpp/layered_face_test.cc']
    digest=hashlib.sha256(b''.join(p.read_bytes() for p in sources)).hexdigest()[:16]
    out=ROOT/'tmp/expression-native'/digest;out.mkdir(parents=True,exist_ok=True)
    exe=out/'face.exe'
    if not exe.exists():
        spec=importlib.util.spec_from_file_location('expression_compiler',ROOT/'scripts/test-expression-e1-26-cpp.py')
        helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
        command,kind=helper.find_compiler()
        cmd=command+(['c++'] if kind=='zig-c++' else [])+['-O2','-std=c++17','-UNDEBUG','-I',str(board),str(sources[-1]),'-o',str(exe)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=180)
        if p.returncode:raise ValueError('生产播放器构建未通过：'+p.stderr[-1000:])
    p=subprocess.run([str(exe),str(Path(path).resolve()),'validate'],capture_output=True,text=True,timeout=120)
    if p.returncode or not json.loads(p.stdout).get('valid'):raise ValueError('生产播放器未通过资源核验')
    return dict(valid=True,production_source_sha256=digest)
