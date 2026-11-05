#!/usr/bin/env python3
"""Build the bounded USB drain in a separate directory, preserving E3c-7."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('e3c8_builder', ROOT / 'scripts/build-deskbot-studio-e3c7-firmware.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
builder.EDITION, builder.PRIOR_EDITION = 'E3c-8', 'E3c-7'
builder.WORK = ROOT / 'tmp/e3c8-usb-drain-20261105'
builder.PUBLIC = ROOT / 'artifacts/deskbot-studio-e3c8-20261105'
builder.PRIOR_REPORT = ROOT / 'artifacts/deskbot-studio-e3c7-20261105/firmware-build-report.json'

if __name__ == '__main__':
    builder.main()
