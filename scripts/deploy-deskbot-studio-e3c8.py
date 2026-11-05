#!/usr/bin/env python3
"""Deploy the bounded USB drain with an exact E3c-7 rollback."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from protocol import studio_e3c8_deployment as reviewed

spec = importlib.util.spec_from_file_location('e3c8_deploy', ROOT / 'scripts/deploy-deskbot-studio-e3c.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry.reviewed = reviewed
entry.ROLLBACK_TARGET = 'E3c-7'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('prepare', 'install', 'restart', 'recover'))
    parser.add_argument('--run', required=True)
    parser.add_argument('--reuse-verified-reference', action='store_true')
    args = parser.parse_args()
    print(json.dumps(entry.run(args.phase, entry.folder_for(args.run),
        reuse_reference=args.reuse_verified_reference), sort_keys=True))
