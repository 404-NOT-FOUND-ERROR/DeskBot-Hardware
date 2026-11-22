"""Use the existing App-only transaction and same-run E3f-9d.1 recovery."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from protocol import display_psram_e3f9d2 as reviewed
spec=importlib.util.spec_from_file_location('display_psram_transport',ROOT/'scripts/deploy-studio-roles-e3f9d.py')
parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
parent.reviewed=reviewed
parent.entry.reviewed=reviewed
parent.entry.base.reviewed=reviewed
parent.entry.base.entry.reviewed=reviewed
parent.entry.base.entry.ROLLBACK_TARGET=reviewed.ROLLBACK_TARGET


def folder(label):
    if not re.fullmatch(r'e3c-live-[0-9]{8}-e3f9d2-[a-z0-9-]{1,24}',label):
        raise ValueError('display_run_label_changed')
    path=ROOT/'tmp'/label
    if path.is_symlink() or path.resolve().parent!=(ROOT/'tmp').resolve():
        raise ValueError('display_run_path_changed')
    return path


def run(phase,label,source=None):
    return parent.entry.base.run(phase,folder(label),source=folder(source) if source else None)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('prepare','check','prepare-live','install','recover','restart'))
    parser.add_argument('--run',required=True);parser.add_argument('--from-run');args=parser.parse_args()
    if (args.phase=='prepare-live')!=(args.from_run is not None):
        parser.error('prepare-live needs --from-run')
    print(json.dumps(run(args.phase,args.run,args.from_run)),flush=True)
