"""Install the locked, small voice runtime into this project's ignored tmp tree."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'integrations/client-4b2b/dependencies.lock.json'
TARGET = ROOT / 'tmp/voice-runtime-4b2b/node_modules'


def install():
    lock = json.loads(LOCK.read_text(encoding='utf-8'))
    TARGET.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({
        'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'}))
    for item in lock['packages']:
        assert item['name'] in ('ws', 'opusscript')
        destination = TARGET / item['name']
        if destination.exists():
            for name, expected in item['files'].items():
                assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected, name
            print(json.dumps({'package': item['name'], 'status': 'verified'}))
            continue
        assert item['tarball'].startswith('https://registry.npmjs.org/')
        with opener.open(item['tarball'], timeout=40) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        assert len(raw) <= 2 * 1024 * 1024
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
        assert 'sha512-' + base64.b64encode(hashlib.sha512(raw).digest()).decode() == item['integrity']
        # Stage beneath the known target; only archive regular files are used.
        with tempfile.TemporaryDirectory(prefix='voice-package-', dir=TARGET) as temporary:
            stage = Path(temporary) / 'package'
            with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
                for member in archive.getmembers():
                    parts = Path(member.name).parts
                    assert parts[0] == 'package' and not member.issym() and not member.islnk()
                    relative = Path(*parts[1:])
                    final = (stage / relative).resolve()
                    assert final.is_relative_to(stage.resolve())
                    if member.isfile():
                        assert relative.as_posix() in item['files']
                        final.parent.mkdir(parents=True, exist_ok=True)
                        final.write_bytes(archive.extractfile(member).read())
            for name, expected in item['files'].items():
                assert hashlib.sha256((stage / name).read_bytes()).hexdigest() == expected, name
            stage.rename(destination)
        print(json.dumps({'package': item['name'], 'status': 'installed'}))


if __name__ == '__main__':
    install()
