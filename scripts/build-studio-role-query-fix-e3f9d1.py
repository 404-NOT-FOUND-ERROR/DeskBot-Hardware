"""Reuse the reviewed paired-build cache and archive the corrected release."""
import argparse
import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('role_fix_build',ROOT/'scripts/build-studio-role-activation-e3f9d.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
base.PUBLIC=ROOT/'artifacts/studio-role-query-fix-e3f9d1'
base.LOG_ROOT=ROOT/'tmp/studio-role-query-fix-e3f9d1'
base.EDITION='E3f-9d.1'

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true');args=parser.parse_args()
    base.LOG_ROOT.mkdir(exist_ok=True);base.PUBLIC.mkdir(exist_ok=True)
    base.build(resume=not args.check,check=args.check)
    for name in ('xiaozhi.bin','xiaozhi.elf'):
        raw=(base.WORK/'on'/name).read_bytes();out=base.LOG_ROOT/name
        if args.check:
            if out.read_bytes()!=raw:raise ValueError('archived role fix artifact changed')
        else:
            with out.open('xb') as stream:stream.write(raw)
