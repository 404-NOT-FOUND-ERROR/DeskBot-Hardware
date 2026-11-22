"""Exercise the real sender process, client HTTP and SQLite across interruption."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError

from protocol.client_head_touch import json_request
from protocol.studio_identity_observation import IdentityObservation
from test_client_head_touch import hello

ROOT = Path(__file__).resolve().parents[1]
CLIENT = os.environ.get("DESKBOT_TEST_CLIENT_ROOT")
NODE = os.environ.get("DESKBOT_TEST_NODE")


def wait_for(check, timeout=12):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = check()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError("resident test timed out")


@unittest.skipUnless(CLIENT and NODE, "set DESKBOT_TEST_CLIENT_ROOT and DESKBOT_TEST_NODE")
class ResidentHTTPTests(unittest.TestCase):
    def test_service_outage_lost_receipt_single_owner_and_restart(self):
        with tempfile.TemporaryDirectory(prefix="head-touch-resident-", dir=ROOT / "tmp") as temporary:
            folder = Path(temporary)
            token_file = folder / "local-token.txt"
            token_file.write_text("a" * 64, encoding="utf-8")
            database = folder / "world.sqlite"
            ready = folder / "ready.json"
            with (folder / "client.err.log").open("ab") as errors:
                node = subprocess.Popen([NODE, str(ROOT / "tests/head-touch-client-fixture.mjs"),
                    CLIENT, str(database), str(token_file), str(ready)], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=errors,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            client_port = wait_for(lambda: json.loads(ready.read_text()).get("port") if ready.exists() else None)
            reader = IdentityObservation(wall=lambda: datetime.now(timezone.utc).isoformat())
            reader.link("connected", "COM4", "single_console_owner")
            reader.feed(('I (10000) DeskBotLink: ' + json.dumps(hello()) + '\n').encode())
            reader.feed(b'I (11000) Touch: Triple PRESS_DOWN detected!\n')
            state = dict(client_online=False, drop_receipt=True, client_calls=[], observer_online=True)

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def reply(self, code, value):
                    raw = json.dumps(value).encode()
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

                def do_GET(self):
                    if self.path == "/api/body/state":
                        self.reply(200, reader.snapshot()) if state["observer_online"] else self.reply(503, {})
                    else:
                        self.reply(200, {"status": "ok"}) if state["client_online"] else self.reply(503, {})

                def do_POST(self):
                    if not state["client_online"]:
                        self.reply(503, {})
                        return
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    state["client_calls"].append(body["uptime_ms"])
                    try:
                        receipt = json_request(f'http://127.0.0.1:{client_port}/api/hardware/head-touch',
                            body, token="a" * 64)
                    except HTTPError as error:
                        state["client_error"] = error.read().decode("utf-8", errors="replace")
                        raise
                    if state["drop_receipt"]:
                        state["drop_receipt"] = False
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    self.reply(200, receipt)

            http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            port = http.server_port
            sender = None

            def start_sender():
                return subprocess.Popen([sys.executable, str(ROOT / "scripts/sync-head-touch.py"),
                    "--folder", str(folder), "--token-file", str(token_file),
                    "--studio-port", str(port), "--client-port", str(port)], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

            def status():
                path = folder / "status.json"
                return json.loads(path.read_text()) if path.exists() else {}

            def stop_sender():
                (folder / "stop").touch()
                sender.wait(timeout=12)
                self.assertEqual(sender.returncode, 0)
                self.assertEqual(status()["run_state"], "stopped")
                (folder / "stop").unlink()

            try:
                sender = start_sender()
                wait_for(lambda: status().get("counts") == {"pending": 1})
                second_process = start_sender()
                second_process.wait(timeout=5)
                self.assertNotEqual(second_process.returncode, 0)
                self.assertEqual(status()["pid"], sender.pid)
                self.assertIsNone(sender.poll())
                stop_sender()
                sender = start_sender()
                state["observer_online"] = False
                state["client_online"] = True
                try:
                    wait_for(lambda: status().get("counts") == {"recorded": 1})
                except AssertionError as error:
                    raise AssertionError(f"{error}; client={state.get('client_error')}; status={status()}") from None
                self.assertEqual(state["client_calls"], [11000, 11000])
                self.assertEqual(status()["studio"], "unavailable")
                state["observer_online"] = True
                wait_for(lambda: status().get("studio") == "connected")
                stop_sender()
                sender = start_sender()
                wait_for(lambda: status().get("counts") == {"recorded": 1}
                    and status().get("pid") == sender.pid)
                self.assertEqual(state["client_calls"], [11000, 11000])
                with sqlite3.connect(database) as archive:
                    count = archive.execute("SELECT count(*) FROM records WHERE namespace='input.events'").fetchone()[0]
                    world = json.loads(archive.execute("SELECT payload_json FROM records WHERE namespace='canonical-world.states'").fetchone()[0])
                archive.close()
                self.assertEqual(count, 1)
                self.assertEqual(len(world["body"]["turns"]), 1)
                self.assertEqual(len([r for r in world["refraction"]["records"] if r["category"] == "body"]), 1)
                self.assertEqual(len([m for m in world["memory"]["episodes"] if m["source"]["kind"] == "body"]), 1)
                self.assertTrue(status()["last_recorded"]["event_id"].startswith("head-touch:"))
            finally:
                if sender is not None and sender.poll() is None:
                    (folder / "stop").touch()
                    sender.wait(timeout=12)
                http.shutdown(); http.server_close(); thread.join(timeout=5)
                node.terminate(); node.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
