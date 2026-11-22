"""E3c-6 IPC bridge. The existing single worker retains USB ownership."""
from copy import deepcopy
from protocol.magnetic_stream import MagneticStream, validate_stream
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
from protocol import studio_installed_catalog as catalog
from protocol import studio_timing as timing
from protocol import studio_device_roles as device_roles
from protocol.presentation_coordinator import PresentationCoordinator

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
                    or not link.allowed_operation(row["operation"])
                    or row["status"] not in ("processed", "rejected")):
                return False
        if "roles" in value: device_roles.validate_snapshot(value["roles"], value)
        if value.get("role_attempt") is not None:
            attempt = value["role_attempt"]
            if (type(attempt) is not dict or set(attempt) != {"command_id", "operation", "request_id"}
                    or not session._id(attempt["command_id"]) or not session._id(attempt["request_id"])
                    or not device_roles.ACTIVATE.fullmatch(attempt["operation"])):
                return False
        directory = value["catalog"]
        if any(directory[key] is not None and directory[key] != value[key]
               for key in ("target_id", "boot_id", "session_id")):
            return False
        if directory["status"] in ("reading", "complete"):
            if not value["enabled"] or any(directory[key] is None
                    for key in ("target_id", "boot_id", "session_id")):
                return False
            if directory["status"] == "reading":
                if (directory["manifest"] is not None or value["can_submit"]
                        or directory["next_cursor"] != directory["received_entries"]
                        or directory["total"] is not None and directory["received_entries"] >= directory["total"]):
                    return False
            else:
                manifest = catalog.validate_manifest(directory["manifest"])
                if (directory["next_cursor"] is not None or directory["received_entries"] != directory["total"]
                        or directory["total"] != len(manifest["entries"])):
                    return False
        elif (directory["manifest"] is not None or directory["received_entries"] != 0
              or directory["total"] is not None or directory["next_cursor"] is not None):
            return False
        if value["can_submit"] and (not value["enabled"] or value["display"] is None
                or value["age_ms"] >= timing.STATE_VALID_MS or value["pending"] is not None
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
        self.network_maintenance = False
        self.magnetic_state = MagneticStream().snapshot(self.control_state)
        self.presentation = PresentationCoordinator(self)

    def _connect(self, port, key):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise ValueError("已有一个 USB 连接，请先断开")
            self.control_state = link.initial_snapshot()
            self.command_pending = None
            self.magnetic_state = MagneticStream().snapshot(self.control_state)
        return super()._connect(port, key)

    def _disconnect(self):
        result = super()._disconnect()
        with self.lock:
            self.control_state = link.aged_control(self.control_state, 0, connected=False)
            self.command_pending = None
            self.magnetic_state = MagneticStream().snapshot(self.control_state)
        return result

    def _read(self, process):
        previous_generated = None
        try:
            while line := process.stdout.readline(65537):
                if len(line) > 65536 or not line.endswith("\n"):
                    break
                try:
                    value = json.loads(line, object_pairs_hook=session._unique)
                    now_ms = int(self.clock()*1000)
                    generated = value.get("generated_at_ms") if isinstance(value, dict) else None
                    if (not isinstance(value, dict) or set(value) not in (
                                {"schema", "observation", "control", "generated_at_ms"},
                                {"schema", "observation", "control", "generated_at_ms", "magnetic"})
                            or value["schema"] != "deskbot.studio_transport/v1"
                            or not timing.monotonic_ms(generated) or generated > now_ms
                            or previous_generated is not None and generated < previous_generated
                            or not self.validate_snapshot(value["observation"])
                            or not validate_control(value["control"])):
                        break
                    magnetic = value.get('magnetic')
                    if magnetic is not None:
                        validate_stream(magnetic)
                        if any(magnetic['identity'][k] != value['control'][k] for k in magnetic['identity']):
                            raise ValueError('magnetic_identity_changed')
                        if any(row['at_ms'] > generated for row in magnetic['events']):
                            raise ValueError('magnetic_clock_invalid')
                    with self.lock:
                        if self.process is not process:
                            return
                        previous_generated = generated
                        # Include validation and lock wait in the same age anchor.
                        received = self.clock()
                        now_ms = int(received*1000)
                        if generated > now_ms:
                            raise ValueError("worker_clock_moved_backwards")
                        self.accessed |= value["observation"]["hardware_accessed"]
                        delay_ms = now_ms - generated
                        self.current = self.age_snapshot(value["observation"], delay_ms)
                        self.received = received
                        self.control_state = link.aged_control(value["control"], delay_ms)
                        self.magnetic_state = deepcopy(magnetic) if magnetic is not None else MagneticStream().snapshot(self.control_state)
                        ack = self.control_state["last_command"]
                        if ack and self.command_pending and ack["command_id"] == self.command_pending["command_id"]:
                            self.command_pending = None
                        self.presentation.observe(self.control_state)
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
                if (self.clock()-self.command_time)*1000 >= timing.IPC_TIMEOUT_MS:
                    row["host_command"]["status"] = "timeout"
            return row

    def magnetic(self):
        """Read the existing owner's event buffer; reading never changes event age."""
        with self.lock:
            control = self.control()
            if control['connection_id'] != self.magnetic_state['identity']['connection_id'] or not control['enabled']:
                return MagneticStream().snapshot(control)
            return deepcopy(self.magnetic_state)

    def shell_inputs(self):
        """One owner transaction; movement stays unknown until its live source is connected."""
        with self.lock:
            return dict(control=self.control(), magnetic=self.magnetic(), motion=None)

    def command(self, value):
        return self.presentation.submit(value)

    def _command(self, value, *, before_write=None):
        value = link.validate_command(value)
        with self.lock:
            if self.network_maintenance:
                raise ValueError('连接设置正在处理，请稍后操作')
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
            if value["operation"] in display.EXPRESSION_SEMANTICS and not current["can_submit"]:
                raise ValueError("等待桌宠回到主页待机后再试")
            if (value["operation"] in display.EXPRESSION_SEMANTICS
                    and not display.supports(current["display"], value["operation"])):
                raise ValueError("当前固件尚未接入这个表情请求")
            raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"))+"\n"
            if len(raw) > 512:
                raise ValueError("请求过长")
            if before_write:
                before_write()
            try:
                self.process.stdin.write(raw)
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise OSError("连接进程已退出") from None
            self.command_pending = dict(command_id=value["command_id"], operation=value["operation"], status="queued")
            self.command_time = self.clock()
            return self.control()
