"""Apply the reviewed 4b1a seam to the pinned client checkout."""
import argparse
from pathlib import Path
import shutil
import subprocess

BASE = '0d8d4c5daf261446735b049c6f385981ffd96b21'
HERE = Path(__file__).resolve().parent


def apply(checkout):
    target = Path(checkout).resolve()
    head = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True).strip()
    if head != BASE:
        raise ValueError('client_baseline_mismatch')
    patch = HERE / 'client-4b1a.patch'
    reverse = subprocess.run(['git','-C',str(target),'apply','--reverse','--check',str(patch)], capture_output=True)
    if reverse.returncode:
        subprocess.run(['git','-C',str(target),'apply','--check',str(patch)], check=True)
        subprocess.run(['git','-C',str(target),'apply',str(patch)], check=True)
    service = target / 'apps/deskbot-service'
    shutil.copyfile(HERE/'chat-turn-recovery.mjs', service/'src/chat-turn-recovery.mjs')
    shutil.copyfile(HERE/'recovery.test.mjs', service/'recovery-4b1a.test.mjs')
    print('Applied 4b1a to', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('checkout', type=Path)
    apply(parser.parse_args().checkout)
