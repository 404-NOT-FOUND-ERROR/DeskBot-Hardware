"""Run actual client HTTPS output through the production C++ wire/control code."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run(client):
    test = client / "apps/deskbot-service/test/wifi-expression.test.mjs"
    node = shutil.which("node")
    if not test.is_file() or not node:
        raise ValueError("client test and Node.js runtime required")
    spec = importlib.util.spec_from_file_location("native_expression_test", ROOT / "tests/test_wifi_expression.py")
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    with tempfile.TemporaryDirectory(prefix="wifi-expression-integration-", dir=ROOT / "tmp") as folder:
        binary = Path(folder) / "core.exe"
        native.compile_native(ROOT / "tests/cpp/wifi_expression_test.cc", binary)
        environment = dict(os.environ, DESKBOT_WIFI_EXPRESSION_NATIVE=str(binary))
        subprocess.run([node, "--test", "--test-name-pattern=HTTPS consumes a real chat output", str(test)],
                       cwd=client, env=environment, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-root", type=Path, required=True)
    run(parser.parse_args().client_root.resolve())
