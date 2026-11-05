"""E3c-6 IPC bridge. The existing single worker retains USB ownership."""
from copy import deepcopy
import json
import subprocess
import jsonschema
from referencing import Registry, Resource

from protocol.studio_identity_bridge import IdentityBridge
from protocol.studio_body_bridge import ROOT
from protocol import studio_control_link as link
from protocol import studio_display_query as display
from protocol import expression_control as expressions
from protocol import studio_usb_session as session

_documents = [json.loads((ROOT / "protocol" / name).read_bytes()) for name in (
    "deskbot-studio-control-link-v1.schema.json", "deskbot-studio-display-v1.schema.json",
    "deskbot-expression-control-v1.schema.json")]
_validator = jsonschema.Draft202012Validator(_documents[0], registry=Registry().with_resources(
    (value["$id"], Resource.from_contents(value)) for value in _documents),
    format_checker=jsonschema.FormatChecker())


def validate_control(value):
    if not _validator.is_valid(value):
        return False
    if (value["schema"] != link.SCHEMA or value["source"] != "usb_session"
            or not session._id(value["connection_id"])
            or type(value["enabled"]) is not bool or type(value["can_submit"]) is not bool
            or value["status"] not in ("off", "pending", "ready", "querying", "busy", "waiting", "disconnected")
            or not session._id(value["reason"])
            or any(value[k] is not None and not session._id(value[k]) for k in ("target_id", "boot_id", "session_id"))
            or any(type(value[k]) is not int or not 0 <= value[k] <= 9007199254740991 for k in ("frames_sent", "rejected_frames"))
            or value["age_ms"] is not None and (type(value["age_ms"]) is not int or value["age_ms"] < 0)
            or not isinstance(value["receipts"], list) or len(value["receipts"]) > 24):
        return False
    try:
        if value["display"] is not None:
            display.validate_reply(value["display"])
            if (value["age_ms"] is None or any(value["display"][key] != value[key]
                    for key in ("target_id", "boot_id", "session_id"))):
                return False
        for row in value["receipts"]:
            expressions.validate_receipt(row)
            if row["source"] != "device":
                return False
        for key, keys in (("pending", {"request_id", "phase", "age_ms"}),
                          ("local_result", {"request_id", "outcome", "reason"}),
                          ("last_command", {"command_id", "operation", "status", "reason"})):
            row = value[key]
            if row is not None and (not isinstance(row, dict) or set(row) != keys):
                return False
        if value["pending"] is not None:
            row = value["pending"]
            if not session._id(row["request_id"]) or row["phase"] not in ("accepted", "waiting") or type(row["age_ms"]) is not int or row["age_ms"] < 0:
                return False
        if value["local_result"] is not None:
            row = value["local_result"]
            if (not session._id(row["request_id"]) or not session._id(row["reason"])
                    or row["outcome"] not in ("timeout", "connection_lost", "cancelled")):
                return False
        if value["last_command"] is not None:
            row = value["last_command"]
            if (not session._id(row["command_id"]) or not session._id(row["reason"])
                    or row["operation"] not in ("enable", "disable", "neutral") or row["status"] not in ("processed", "rejected")):
                return False
        if value["can_submit"] and (not value["enabled"] or value["display"] is None
                or value["age_ms"] >= 2000 or value["pending"] is not None
                or value["session_id"] is None
                or display.reason(value["display"]) != "sleep_unknown"):
            return False
    except (ValueError, TypeError, KeyError):
        return False
    return True


class ControlBridge(IdentityBridge):
    def __init__(self, python=None, *, popen=subprocess.Popen, **kwargs):
        def spawn(*args, **options):
            return popen(*args, stdin=subprocess.PIPE, **options)
        super().__init__(python, popen=spawn, **kwargs)
        self.worker = ROOT / "tools/observe-studio-body-e3c.py"
        self.control_state = link.initial_snapshot()
        self.command_pending = None
        self.command_time = 0

    def _connect(self, port, key):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("已有一个 USB 连接，请先断开")
            self.control_state = link.initial_snapshot()
            self.command_pending = None
        return super()._connect(port, key)

    def _disconnect(self):
        result = super()._disconnect()
        with self.lock:
            self.control_state = link.aged_control(self.control_state, 0, connected=False)
            self.command_pending = None
        return result

    def _read(self, process):
        try:
            while line := process.stdout.readline(65537):
                if len(line) > 65536 or not line.endswith("\n"):
                    break
                try:
                    value = json.loads(line, object_pairs_hook=session._unique)
                    if (not isinstance(value, dict) or set(value) != {"schema", "observation", "control"}
                            or value["schema"] != "deskbot.studio_transport/v1"
                            or not self.validate_snapshot(value["observation"])
                            or not validate_control(value["control"])):
                        break
                    with self.lock:
                        if self.process is not process:
                            return
                        self.accessed |= value["observation"]["hardware_accessed"]
                        self._update(value["observation"])
                        self.control_state = value["control"]
                        ack = self.control_state["last_command"]
                        if ack and self.command_pending and ack["command_id"] == self.command_pending["command_id"]:
                            self.command_pending = None
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
            if process.stdin:
                process.stdin.close()
            with self.lock:
                if self.process is process:
                    self._update(self.age_snapshot(self.current, (self.clock()-self.received)*1000, worker_alive=False))
                    self.control_state = link.aged_control(self.control_state, 0, connected=False)
                    self.command_pending = None

    def control(self):
        with self.lock:
            connected = self.snapshot()["connection"]["status"] == "connected" and self.process is not None
            row = link.aged_control(self.control_state, (self.clock()-self.received)*1000, connected=connected)
            row["host_command"] = deepcopy(self.command_pending)
            if self.command_pending:
                row["can_submit"] = False
                if self.clock()-self.command_time > 3:
                    row["host_command"]["status"] = "timeout"
            return row

    def command(self, value):
        value = link.validate_command(value)
        with self.lock:
            current = self.control()
            if value["connection_id"] != current["connection_id"]:
                raise ValueError("连接已更新，请等待状态刷新")
            if self.process is None or self.process.poll() is not None or self.snapshot()["connection"]["status"] != "connected":
                raise ValueError("请先连接本体 USB")
            if self.command_pending:
                if self.command_pending["command_id"] == value["command_id"]:
                    return current
                raise ValueError("上一项请求仍在处理中")
            ack = current["last_command"]
            if ack and ack["command_id"] == value["command_id"]:
                return current
            if value["operation"] == "neutral" and not current["can_submit"]:
                raise ValueError("等待桌宠回到主页待机后再试")
            raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"))+"\n"
            if len(raw) > 512:
                raise ValueError("请求过长")
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise OSError("连接进程已退出") from None
            self.command_pending = dict(command_id=value["command_id"], operation=value["operation"], status="queued")
            self.command_time = self.clock()
            return self.control()
