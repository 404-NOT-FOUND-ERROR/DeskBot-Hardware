"""One passive console worker, isolated from the Studio packager runtime."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import threading
import time

from protocol.body_console_observation import Observation, aged_snapshot, SCHEMA

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "tools/observe-studio-body-e3a.py"


class ConsoleBridge:
    def __init__(self, python=None, *, run=subprocess.run, popen=subprocess.Popen, clock=time.monotonic,
                 worker=WORKER, observation_factory=Observation, snapshot_schema=SCHEMA,
                 age_snapshot=aged_snapshot, validate_snapshot=None):
        self.python = Path(python) if python else None
        self.run, self.popen, self.clock = run, popen, clock
        self.worker, self.observation_factory = Path(worker), observation_factory
        self.snapshot_schema, self.age_snapshot = snapshot_schema, age_snapshot
        self.validate_snapshot = validate_snapshot
        self.lock = threading.RLock()
        self.operation = threading.Lock()
        self.process = self.thread = None
        self.observation = self.observation_factory(clock=clock)
        self.current, self.received = self.observation.snapshot(), clock()
        self.accessed = False

    def available(self):
        return self.python is not None and self.python.is_file()

    def ports(self):
        if not self.available():
            return {"available": False, "reason": "serial_runtime_required", "ports": []}
        try:
            result = self.run([str(self.python), str(self.worker), "--list"], cwd=ROOT,
                              capture_output=True, text=True, timeout=8, check=False)
            data = json.loads(result.stdout)
            if result.returncode or data.get("schema") != "deskbot.console_ports/v1":
                raise ValueError("serial runtime")
            rows = data["ports"]
            if not isinstance(rows, list) or len(rows) > 32:
                raise ValueError("port count")
            for row in rows:
                if (not isinstance(row, dict) or set(row) != {"port", "usb_id", "key"}
                        or not re.fullmatch(r"COM[1-9][0-9]{0,3}", row["port"])
                        or row["usb_id"] != "303A:1001" or not re.fullmatch(r"[a-f0-9]{64}", row["key"])):
                    raise ValueError("port format")
            return {"available": True, "reason": "", "ports": rows}
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            return {"available": False, "reason": "serial_runtime_unavailable", "ports": []}

    def _update(self, value):
        self.current, self.received = value, self.clock()

    def _read(self, process):
        try:
            while line := process.stdout.readline(65_537):
                if len(line) > 65_536 or not line.endswith("\n"):
                    break
                try:
                    data = json.loads(line)
                    if (data.get("schema") != self.snapshot_schema or data.get("source") != "usb_console"
                            or data.get("read_only") is not True or data.get("device_written") is not False
                            or len(data.get("events", [])) > 24):
                        break
                    if self.validate_snapshot is not None and not self.validate_snapshot(data):
                        break
                    with self.lock:
                        if self.process is not process:
                            return
                        self.accessed |= data["hardware_accessed"]
                        self._update(data)
                except (ValueError, TypeError, KeyError):
                    break
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            process.stdout.close()
            with self.lock:
                if self.process is process:
                    self._update(self.age_snapshot(self.current, (self.clock() - self.received) * 1000, worker_alive=False))

    def connect(self, port, key):
        with self.operation:
            return self._connect(port, key)

    def _connect(self, port, key):
        if not isinstance(port, str) or not isinstance(key, str):
            raise ValueError("请选择本次枚举到的 USB 串口")
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("已有一个观察连接，请先断开")
            listing = self.ports()
            if not listing["available"]:
                raise ValueError("请使用已核对、包含 pyserial 的 Python 启动串口观察服务")
            if not any(row["port"] == port and row["key"] == key for row in listing["ports"]):
                raise ValueError("USB 设备已变化，请刷新端口后选择")
            self.observation = self.observation_factory(clock=self.clock)
            self.observation.link("connecting", port, "opening_passive_console")
            self._update(self.observation.snapshot())
            try:
                process = self.popen([str(self.python), "-u", str(self.worker), "--port", port, "--key", key],
                                     cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                self.observation.link("error", port, "reader_unavailable")
                self._update(self.observation.snapshot())
                raise
            self.process = process
            self.thread = threading.Thread(target=self._read, args=(process,), daemon=True)
            self.thread.start()
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            alive = self.process is None or self.process.poll() is None
            result = self.age_snapshot(self.current, (self.clock() - self.received) * 1000, worker_alive=alive)
            result["hardware_accessed"] |= self.accessed
            return result

    def disconnect(self):
        with self.operation:
            return self._disconnect()

    def _disconnect(self):
        with self.lock:
            process, thread = self.process, self.thread
            self.process = self.thread = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            if thread is not None:
                thread.join(timeout=4)
        with self.lock:
            value = self.age_snapshot(self.current, (self.clock() - self.received) * 1000)
            value["connection"] = {"status": "disabled", "port": None, "reason": "observer_stopped"}
            value["freshness"], value["live"] = "disabled", False
            value = self.age_snapshot(value, 0)
            self._update(value)
            return self.snapshot()

    close = disconnect
