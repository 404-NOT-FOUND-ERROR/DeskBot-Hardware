"""Synchronize one owner-confirmed saved installation with a local client."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from protocol.client_installation_sync import InstallationHTTP, sync_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workbench', 'client', 'source-id', 'character-id', 'device-id'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    try:
        result = sync_once(InstallationHTTP(args.workbench), InstallationHTTP(args.client),
            source_id=args.source_id, character_id=args.character_id, device_id=args.device_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] == 'synchronized' else 2
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps(dict(status='failed', error=str(error)), ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
