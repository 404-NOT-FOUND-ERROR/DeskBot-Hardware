#!/usr/bin/env python3
"""Export pinned official expression/behavior evidence; never contact a device.

The route descriptions are reviewed interpretations guarded by exact source
anchors. The call inventory is lexical, not a compiler or reachability proof.
Official evidence is read from Git objects, never from a dirty working tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "deskbot.behavior_bindings.v1"
COMMIT = "46d427255756fe2a148777ea3f256b615b52e160"
REPO = ROOT / "tmp/remote-esp-vocat"
PREFIX = "examples/xiaozhi-esp32/"
CURRENT = ROOT / "tmp/o2f2-o2d-readonly-20261018/source"
BOARD = "main/boards/esp_vocat/"
APP = "main/application.cc"
DISPLAY = "main/display/emote_display.cc"
EMOTE = BOARD + "assets/360_360/emote.json"
LAYOUT = BOARD + "assets/360_360/layout.json"
COMPONENT = "managed_components/espressif2022__esp_emote_expression/"
OP = COMPONENT + "src/emote_op.c"
SETUP = COMPONENT + "src/emote_setup.c"
DEFS = COMPONENT + "priv_include/emote_defs.h"
ACTION_NAMES = {
    "SHARK_HEAD": ("shark_head", "摇头", 0),
    "SHARK_HEAD_DECAY": ("shark_head_decay", "缓慢摇头", 1),
    "LOOK_AROUND": ("look_around", "好奇环顾", 2),
    "BEAT_SWING": ("beat_swing", "节拍摇摆", 3),
    "CAT_NUZZLE": ("cat_nuzzle", "蹭头撒娇", 4),
}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.PIPE)


def clean_eol(raw: bytes) -> bytes:
    return raw.replace(b"\r\n", b"\n")


def mask_comments(text: str) -> str:
    """Mask comments, preserving strings and character offsets/line numbers."""
    pattern = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: re.sub(r"[^\n]", " ", m[0])
                  if m[0].startswith(("//", "/*")) else m[0], text)


def call_args(text: str, opening: int) -> tuple[list[str], int]:
    """Split the bounded call's arguments, respecting nested calls/strings."""
    args: list[str] = []
    start, depth, quote, escaped = opening + 1, 1, "", False
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
        elif ch in ('"', "'"):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append(text[start:i].strip())
                return args, i + 1
        elif ch == "," and depth == 1:
            args.append(text[start:i].strip())
            start = i + 1
    raise ValueError("unterminated source call")


def literal_string(value: str) -> str | None:
    if not re.fullmatch(r'"[^"\\]*"', value):
        return None
    return value[1:-1]


def scan_calls(path: str, text: str) -> list[dict[str, Any]]:
    """Collect expression entry calls only. Commented calls are not evidence."""
    masked = mask_comments(text)
    code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  lambda m: re.sub(r"[^\n]", " ", m[0]), masked)
    pattern = r"(?:->|\.)(SetEmotion|InsertAnimDialog)\s*\(|(?<![:\w])(Alert)\s*\("
    records = []
    for match in re.finditer(pattern, code):
        api = match[1] or match[2]
        args, end = call_args(masked, match.end() - 1)
        index = 2 if api == "Alert" else 0
        if len(args) <= index:
            raise ValueError(f"unexpected call shape: {path}:{api}")
        value = literal_string(args[index])
        line = masked.count("\n", 0, match.start()) + 1
        records.append({"id": f"call:{path}:{line}", "path": path, "line": line,
                        "end_line": masked.count("\n", 0, end) + 1,
                        "api": api, "arguments": args, "semantic": value,
                        "kind": "literal" if value is not None else "dynamic"})
    return records


class Evidence:
    def __init__(self, repo: Path, current: Path):
        self.repo, self.current = repo.resolve(), current.resolve()
        self.texts: dict[str, str] = {}
        self.inputs: dict[str, dict[str, Any]] = {}
        self.items: dict[str, dict[str, Any]] = {}

    def load(self, path: str, component: bool = False) -> str:
        if path in self.texts:
            return self.texts[path]
        if component:
            raw = (self.current / path).read_bytes()
            checks_path = self.current / COMPONENT / "CHECKSUMS.json"
            checks = json.loads(checks_path.read_text(encoding="utf-8"))
            member = path.removeprefix(COMPONENT)
            expected = next((x["hash"] for x in checks["files"] if x["path"] == member), None)
            if digest(raw) != expected:
                raise ValueError(f"component file differs from registry checksum: {member}")
            info = {"origin": "o2f2_component_snapshot", "sha256": digest(raw),
                    "registry_checksum_match": True,
                    "checksum_manifest_sha256": digest(checks_path.read_bytes()),
                    "matches_official_commit": None, "working_tree_matches": None}
        else:
            raw = git(self.repo, "show", COMMIT + ":" + PREFIX + path)
            local = self.current / path
            current_raw = local.read_bytes() if local.is_file() else None
            working = self.repo / PREFIX / path
            info = {"origin": "official_git_blob", "commit": COMMIT, "sha256": digest(raw),
                    "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                    "current_o2f2_sha256": digest(current_raw) if current_raw is not None else None,
                    "current_o2f2_matches": clean_eol(raw) == clean_eol(current_raw) if current_raw is not None else None,
                    "working_tree_matches": clean_eol(raw) == clean_eol(working.read_bytes()) if working.is_file() else None}
        self.inputs[path] = {"path": path, **info}
        self.texts[path] = raw.decode("utf-8")
        return self.texts[path]

    def add(self, path: str, anchor: str, component: bool = False) -> str:
        text = self.load(path, component)
        if text.count(anchor) != 1:
            raise ValueError(f"source anchor is missing or ambiguous: {path}: {anchor!r}")
        begin = text.index(anchor)
        start_line = text.count("\n", 0, begin) + 1
        end_line = start_line + anchor.count("\n")
        ident = "ev:" + digest((path + "\0" + anchor).encode())[:16]
        self.items[ident] = {"id": ident, "path": path, "line": start_line, "end_line": end_line,
                             "excerpt": anchor, "origin": self.inputs[path]["origin"],
                             "source_sha256": self.inputs[path]["sha256"]}
        return ident

    def at(self, path: str, first: int, last: int) -> str:
        text = self.load(path)
        lines = text.splitlines()
        if first < 1 or last < first or last > len(lines):
            raise ValueError(f"invalid evidence line span: {path}:{first}-{last}")
        excerpt = "\n".join(lines[first - 1:last])
        ident = "ev:" + digest(f"{path}\0{first}:{last}\0{excerpt}".encode())[:16]
        self.items[ident] = {"id": ident, "path": path, "line": first, "end_line": last,
                             "excerpt": excerpt, "origin": self.inputs[path]["origin"],
                             "source_sha256": self.inputs[path]["sha256"]}
        return ident


def build(repo: Path = REPO, current: Path = CURRENT) -> dict[str, Any]:
    if git(repo, "rev-parse", COMMIT + "^{commit}").decode().strip() != COMMIT:
        raise ValueError("pinned official commit unavailable")
    ev = Evidence(repo, current)
    # Enumerate tracked SOURCE paths only. Never scan generated build directories.
    # EspS3Cat derives from WifiBoard. Other common boards (e.g. ML307) are not
    # part of this target's graph even when their generic alert API is similar.
    scope = [PREFIX + APP, PREFIX + "main/boards/esp_vocat",
             PREFIX + "main/boards/common/wifi_board.cc"]
    source_paths = [p.removeprefix(PREFIX) for p in
                    git(repo, "ls-tree", "-r", "--name-only", COMMIT, "--", *scope).decode().splitlines()
                    if p.endswith((".cc", ".cpp")) and "/images/" not in p]
    calls = []
    for path in source_paths:
        calls.extend(scan_calls(path, ev.load(path)))
    ev.load(DISPLAY)
    manifest = json.loads(ev.load(EMOTE))
    layout = json.loads(ev.load(LAYOUT))
    if len({item["emote"] for item in manifest}) != len(manifest):
        raise ValueError("duplicate official semantic")
    map_text = ev.load(DISPLAY)
    block = re.search(r"emotion_to_action_map\s*=\s*\{(.*?)\n\s*\};", map_text, re.S)
    if block is None:
        raise ValueError("official emotion/action map not found")
    pairs = re.findall(r'\{"([a-z_]+)",\s*VOCAT_BASE_CMD_SET_ACTION_([A-Z_]+)\}', block[1])
    if len({p[0] for p in pairs}) != len(pairs) or not pairs:
        raise ValueError("invalid official emotion/action map")
    emotion_actions = {}
    map_evidence = {}
    for semantic, suffix in pairs:
        if suffix not in ACTION_NAMES:
            raise ValueError("unreviewed action in source map: " + suffix)
        emotion_actions[semantic] = ACTION_NAMES[suffix][0]
        match = re.search(r'\{"' + semantic + r'",\s*VOCAT_BASE_CMD_SET_ACTION_' + suffix + r'\}', block[1])
        map_evidence[semantic] = ev.add(DISPLAY, match[0])
    display_ev = ev.add(DISPLAY, "emote_set_anim_emoji(emote_handle_, emotion);")
    motor_ev = ev.add(DISPLAY, "vocat_base_control_set_action(it->second);")
    eye_ev = ev.add(OP, "return emote_set_emoji_animation(handle, EMOTE_DEF_OBJ_ANIM_EYE, name);", True)
    dialog_ev = ev.add(OP, "return emote_set_emoji_animation(handle, EMOTE_DEF_OBJ_ANIM_EMERG_DLG, name);", True)
    loop_ev = ev.add(OP, "gfx_anim_set_segment(obj, 0, 0xFFFF, emoji->fps > 0 ? emoji->fps : EMOTE_DEF_ANIMATION_FPS, emoji->loop);", True)
    ev.add(SETUP, 'gfx_anim_set_auto_mirror(obj, true);', True)
    listen_ev = ev.add(OP, "ret = emote_set_icon_animation(handle, EMOTE_DEF_OBJ_ANIM_LISTEN, EMOTE_ICON_LISTEN, 15, true);", True)
    listen_name_ev = ev.add(DEFS, '#define EMOTE_ICON_LISTEN               "listen"', True)
    semantics = []
    eaf_assets = {}
    asset_names = {item["src"] for item in manifest}
    asset_names.add("listen.eaf")
    for name in sorted(asset_names):
        if Path(name).name != name or not name.lower().endswith(".eaf"):
            raise ValueError(f"unsafe EAF asset path: {name}")
        path = BOARD + "assets/emoji_large/" + name
        raw = git(repo, "show", COMMIT + ":" + PREFIX + path)
        current_asset = current / path
        current_raw = current_asset.read_bytes() if current_asset.is_file() else None
        eaf_assets[name] = {"path": path, "bytes": len(raw), "sha256": digest(raw),
                            "git_blob": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                            "current_o2f2_sha256": digest(current_raw) if current_raw is not None else None,
                            "current_o2f2_matches": current_raw == raw if current_raw is not None else None}
    for item in manifest:
        ident = item["emote"]
        evidence = ev.add(EMOTE, next(line.strip() for line in ev.texts[EMOTE].splitlines()
                                      if re.search(r'"emote"\s*:\s*"' + re.escape(ident) + '"', line)))
        local = [c["id"] for c in calls if c["semantic"] == ident]
        semantics.append({"id": ident, "src": item["src"], "fps": item["fps"], "loop": item["loop"],
                          "evidence": [evidence], "set_emotion_action": emotion_actions.get(ident),
                          "action_evidence": [map_evidence[ident], motor_ev] if ident in emotion_actions else [],
                          "literal_call_ids": local,
                          "reachability": "literal_local_entry" if local else "dynamic_entry_only_in_scope",
                          "runtime_observation": "not_collected"})
    semantics.append({"id": "listen", "src": "listen.eaf", "fps": 15, "loop": True,
                      "evidence": [listen_ev, listen_name_ev], "set_emotion_action": None,
                      "action_evidence": [], "literal_call_ids": [],
                      "reachability": "status_entry", "runtime_observation": "not_collected"})
    sem_by_id = {item["id"]: item for item in semantics}
    actions = [{"id": ident, "label": label, "wire_command": 2, "wire_value": value,
                "physical_completion": "unobserved"} for ident, label, value in ACTION_NAMES.values()]
    routes: list[dict[str, Any]] = []

    def route(ident: str, title: str, group: str, condition: str, note: str,
              steps: list[tuple[str, str, str, list[str]]], outputs: list[dict[str, Any]] | None = None,
              action_ids: list[str] | None = None, resolution: str = "source_conditioned") -> None:
        routes.append({"id": ident, "title": title, "group": group, "condition": condition, "note": note,
                       "steps": [{"kind": k, "label": label, "detail": detail, "evidence": evidence}
                                 for k, label, detail, evidence in steps],
                       "outputs": outputs or [], "action_ids": action_ids or [],
                       "resolution": resolution, "runtime_observation": "not_collected"})

    def output(semantic: str, api: str = "SetEmotion", duration: int | None = None) -> dict[str, Any]:
        item = sem_by_id[semantic]
        return {"semantic": semantic, "api": api, "src": item["src"], "fps": item["fps"],
                "loop": item["loop"], "layout": "listen_anim" if semantic == "listen" else
                "emerg_dlg" if api == "InsertAnimDialog" else "eye_anim", "duration_ms": duration,
                "asset": eaf_assets.get(item["src"]),
                "action_id": emotion_actions.get(semantic) if api == "SetEmotion" else None,
                "evidence": item["evidence"] + [listen_ev] if semantic == "listen" else
                item["evidence"] + [dialog_ev if api == "InsertAnimDialog" else eye_ev, loop_ev]}

    # Route descriptions are guarded by unique, exact anchors. Never infer a
    # deterministic animation from WakeWordInvoke or from a natural-language request.
    touch_ev = ev.at(BOARD + "touch_sensor.cc", 44, 75)
    wake_ev = ev.at(APP, 793, 820)
    cloud_ev = ev.at(APP, 484, 491)
    route("pet-head", "摸头三次", "触摸与语音", "1.5 秒内三次接触，且设备为 Idle。",
          "本地先发送“我在摸你猫头”的唤醒上下文；最终说什么、显示何种情绪由服务端决定，不是硬编码 embarrassed。",
          [("event", "顶部电容触摸", "三次 PRESS_DOWN；不是屏幕按钮或长按", [touch_ev]),
           ("call", "WakeWordInvoke", "唤醒/打开音频通道，发送上下文（受编译配置约束）", [wake_ev]),
           ("external", "服务端回应", "云端 llm.emotion 是动态值，本轮没有运行记录", [cloud_ev]),
           ("call", "SetEmotion(动态值)", "可能同时触发表情与动作", [display_ev, motor_ev])],
          resolution="server_dependent")
    route("cloud-emotion", "对话返回情绪", "触摸与语音", "收到 type=llm 且 emotion 为字符串。",
          "28 个登记语义都存在动态入口的可能性；未在本地固定调用，不等于无用或从未播放。",
          [("event", "llm.emotion", "由服务端选择名称", [cloud_ev]),
           ("call", "应用调度 → SetEmotion", "先查动画；另一路查情绪动作表", [display_ev, motor_ev]),
           ("semantic", "动态情绪名称", "可在右侧按语义预览；不是运行结果", [cloud_ev])],
          resolution="dynamic_semantic")
    tap_ev = ev.at(BOARD + "ui_bridge.cc", 315, 328)
    toggle_ev = ev.at(APP, 269, 293)
    route("screen-tap", "主页点击与对话切换", "触摸与语音", "主页 LV_EVENT_CLICKED；非启动配网分支。",
          "Idle 打开聆听，Speaking 中断播报，Listening 关闭通道；不保证每次点击都显示同一种动画。",
          [("event", "圆屏主页点击", "只读分析现有点击路由", [tap_ev]),
           ("call", "ToggleChatState", "按会话状态决定下一步", [toggle_ev]),
           ("semantic", "聆听状态（条件分支）", "眼睛 neutral，顶部 listen 条", [ev.at(APP, 699, 710)])],
          [output("neutral"), output("listen", "status_event")], resolution="state_dependent")
    for ident, title, first, last, note in [
        ("state-idle", "对话待命 / 回到 Idle", 687, 693, "STANDBY 不等同 sleepy；这段源码明确设置 neutral。"),
        ("state-connect", "连接对话通道", 694, 698, "连接状态仍使用 neutral，不是独立连接动画。"),
        ("state-listen", "开始聆听", 699, 710, "neutral 是眼睛；listen 是独立顶部条，两者不是同一资源。"),
    ]:
        state_ev = ev.at(APP, first, last)
        route(ident, title, "系统状态", "应用进入相应 DeviceState。", note,
              [("event", title, "状态切换而不是角色切换", [state_ev]),
               ("call", "SetStatus + SetEmotion(neutral)", "neutral → winking.eaf（非循环）", [state_ev])],
              [output("neutral")] + ([output("listen", "status_event")] if ident == "state-listen" else []))
    route("state-speak", "开始说话", "系统状态", "应用进入 Speaking。",
          "只设置说话状态和图标；这段状态分支本身不选择某个 EAF，情绪由独立的 llm 消息决定。",
          [("event", "Speaking", "不虚构一条“说话 → happy”的绑定", [ev.at(APP, 711, 721)]),
           ("call", "SetStatus(SPEAKING)", "更新扬声器/字幕等 UI，非直接换眼睛", [ev.at(DISPLAY, 199, 209)])])
    base = BOARD + "base_control.cc"
    for ident, title, semantic, first, last, duration in [
        ("base-insert", "底座首次上线 / 重连", "insert", 184, 198, 3000),
        ("fish-attach", "小鱼配件吸附", "eat", 151, 154, 3500),
    ]:
        event_ev = ev.at(base, first, last)
        route(ident, title, "底座事件", "相应 UART 事件被官方 HandleCommand 接收。",
              "使用居中的临时插入动画；不走 SetEmotion 的情绪动作表。底座实际事件产生需硬件验证。",
              [("event", title, "官方 UART 事件分支", [event_ev]),
               ("call", f'InsertAnimDialog("{semantic}")', f"持续 {duration} ms 后退出插入层", [event_ev])],
              [output(semantic, "InsertAnimDialog", duration)])
    pair_ev = ev.at(base, 155, 159)
    route("pair-detect", "底座配对检测", "底座事件", "收到 PAIR_DETECT。",
          "SetEmotion(happy) 先发摇头，再显式发好奇环顾。源码存在两个动作请求，不代表两个动作均完成或确定覆盖顺序。",
          [("event", "PAIR_DETECT", "UART 条件分支", [pair_ev]),
           ("call", 'SetEmotion("happy")', "Happy.eaf；动作表另发 shark_head", [pair_ev, map_evidence["happy"]]),
           ("action", "显式 LOOK_AROUND", "同一分支还发送环顾请求", [pair_ev])],
          [output("happy")], ["shark_head", "look_around"])
    for ident, title, first, last, note in [
        ("slide-down", "磁吸滑块下滑", 115, 118, "原生 ToggleChatState 会按当前状态改变会话；识别事实与这个副作用是两件事。"),
        ("slide-up", "磁吸滑块上滑", 119, 127, "番茄钟页重启 5 分钟；其他页调用 ToggleChatState。"),
        ("slide-click", "磁吸滑块单击", 128, 135, "只有木鱼页调用木鱼点击；没有直接指定 EAF。"),
        ("calibration", "磁吸校准阶段通知", 136, 150, "只更新校准提示文字与完成信号量，不直接触发表情。"),
        ("action-done", "底座动作完成通知", 172, 179, "只记录完成；协议此处没有请求 ID，不能关联具体是哪一个动作。"),
        ("base-offline", "底座心跳超时离线", 229, 235, "只改变在线标记并记日志，本分支没有离线 EAF。"),
    ]:
        event_ev = ev.at(base, first, last)
        route(ident, title, "底座事件", "相应分支条件满足。", note,
              [("event", title, "官方底座接收 / 超时检查", [event_ev]),
               ("call", "原有副作用", note, [event_ev])], resolution="state_dependent" if "slide" in ident else "source_conditioned")
    audio = BOARD + "audio_analysis.cc"
    beat_ev = ev.at(audio, 68, 79)
    route("beat-detect", "检测到音乐节拍", "声音与动作", "已切到 BEAT_DETECTION 模式，收到 BEAT_DETECTED；音频入口还要求 DUMMY 主页。",
          "显式发节拍摇摆，并插入 beat_swing 动画 8 秒；不通过 SetEmotion 动作表。",
          [("event", "检测到节拍", "不是默认 DOA 模式", [beat_ev, ev.at(audio, 144, 155)]),
           ("action", "BEAT_SWING", "一次底座动作请求，物理完成未观测", [beat_ev]),
           ("call", 'InsertAnimDialog("beat_swing")', "居中播放，8 秒插入时长与 EAF 循环参数不同", [beat_ev])],
          [output("beat_swing", "InsertAnimDialog", 8000)], ["beat_swing"])
    doa_ev = ev.at(audio, 81, 86)
    route("doa-follow", "听声转头 / DOA", "声音与动作", "DUMMY 主页 + DOA_FOLLOW 模式 + Idle/Listening；有效角度回调。",
          "直接发送角度，不会自动切换 look_left/look_right EAF；语音方向与表情名称不可混为一谈。",
          [("event", "双麦音频 + VAD", "默认模式和运行状态需一起满足", [ev.at(audio, 164, 172)]),
           ("call", "DoaTrackerResultCallback(angle)", "角度估计来自原组件", [doa_ev]),
           ("action", "set_angle(angle)", "不是 set_action，也没有直接 EAF", [doa_ev])])
    tools_ev = ev.at(BOARD + "dev_tools.cc", 42, 91)
    route("tool-action", "语音工具请求底座动作", "声音与动作", "服务端调用 self.echo_base.set_action，action 是五个预设动作之一。",
          "工具直接发底座动作，不自动播放同名 EAF。calibrate 是磁吸校准；go_home 是返回 UI 主页，不是机械回零。",
          [("external", "Agent / MCP 请求", "自然语言如何选工具由服务端决定", [tools_ev]),
           ("call", "self.echo_base.set_action", "五种动作分别映射协议值 0..4", [tools_ev]),
           ("action", "vocat_base_control_set_action", "请求成功不等于机械动作完成", [tools_ev])],
          action_ids=[x[0] for x in ACTION_NAMES.values()], resolution="server_dependent")
    alarm_ev = ev.at(BOARD + "customer_ui/alarm_end.cc", 78, 99)
    route("alarm-end", "闹钟 / 番茄钟到时", "系统状态", "alarm_time_up_set_origin 的睡眠闹钟或番茄钟分支。",
          "发送“闹钟时间到”或“番茄钟时间到”给会话，唤醒入口受设备状态限制；自然提醒话术不在本地硬编码。",
          [("event", "计时到时", "来源页决定唤醒上下文", [alarm_ev]),
           ("call", "WakeWordInvoke", "后续情绪由服务端选择", [alarm_ev, wake_ev]),
           ("external", "动态回应", "没有确定 EAF 绑定", [cloud_ev])], resolution="server_dependent")
    semantic_notes = {
        "embarrassed": "复用 Happy.eaf；只有经过 SetEmotion 时才发蹭头动作。摸头三次不硬编码 embarrassed。",
        "sleepy": "sleep.eaf 存在且循环；审计范围内未找到“自动休眠 → sleepy”的本地固定调用，不能承诺自动触发。",
        "idle": "idle → neutral.eaf；应用 STANDBY 的固定调用却是 neutral → winking.eaf。名称不是状态机。",
        "look_left": "左看动画资源不等于 DOA 向左转头；DOA 回调不直接调用本语义。",
        "look_right": "右看动画资源不等于 DOA 向右转头；DOA 回调不直接调用本语义。",
        "look_around": "同名动画与底座环顾动作是两份独立定义；工具动作不会自动播放本动画。",
        "tried": "保留官方原始拼写 tried，不擅自改名 tired；无本地固定调用不代表不能动态播放。",
        "winking": "winking → neutral.eaf 并循环；neutral → winking.eaf 且不循环，名称正好交叉。",
    }
    for semantic in sem_by_id:
        item = sem_by_id[semantic]
        note = semantic_notes.get(semantic,
            "资源登记和 SetEmotion 可动态入口均存在；源码局部调用点缺失不等于未使用，也不能据此清理。")
        status_only = semantic == "listen"
        if status_only:
            note = "listen 是状态处理器按图标名称查找的独立动画，不是 emote.json 的情绪语义；按顶部非镜像布局循环。"
        route("semantic-" + semantic, "语义审阅 · " + semantic, "资源反查", "只有相应入口实际请求这个语义时。", note,
              [("semantic", semantic, "资源存在不等于某行为会自动触发", item["evidence"]),
               ("call", "聆听状态专用入口" if status_only else "假设通过 SetEmotion 请求",
                "只用于读懂本地映射，不是已发生事实", [listen_ev] if status_only else [display_ev, motor_ev])],
              [output(semantic, "status_event" if status_only else "SetEmotion")],
              [emotion_actions[semantic]] if semantic in emotion_actions else [],
              resolution="mapping_only")
    missing = sorted({c["semantic"] for c in calls if c["semantic"] and c["semantic"] not in sem_by_id})
    for name in missing:
        sites = [c for c in calls if c["semantic"] == name]
        evidence = [ev.at(c["path"], c["line"], c["end_line"]) for c in sites]
        alert_entry = any(c["api"] == "Alert" for c in sites)
        route("missing-" + name, "清单未登记 · " + name, "映射待核对", "官方调用点传入此名称。",
              ("Alert() 的第三个参数被原样传给 SetEmotion；这个名称不在 emote.json，且不等于显示层 status icon。" if alert_entry else
               "当前固定 emote.json 没有这个语义；此路径不能据此预览。属于清单/调用差异，不代表已复现实机故障。"),
              [("event", "调用点存在", "可能是开机、告警、升级等条件路径", evidence),
               ("semantic", name, "当前 28 项语义清单中未找到 EAF 映射", evidence)], resolution="unmapped")

    for c in calls:
        c["evidence"] = [ev.at(c["path"], c["line"], c["end_line"])]
        c["mapping"] = "dynamic" if c["semantic"] is None else "mapped" if c["semantic"] in sem_by_id else "missing"
    shared: dict[str, list[str]] = {}
    for item in semantics:
        shared.setdefault(item["src"], []).append(item["id"])
    nodes, edges = [], []
    for r in routes:
        previous = None
        for index, step in enumerate(r["steps"]):
            node_id = r["id"] + ":" + str(index)
            nodes.append({"id": node_id, "route_id": r["id"], **step})
            if previous:
                edges.append({"from": previous, "to": node_id, "kind": "reviewed_source_chain",
                              "evidence": step["evidence"], "runtime_observation": "not_collected"})
            previous = node_id
        for index, out in enumerate(r["outputs"]):
            node_id = r["id"] + ":output:" + str(index)
            nodes.append({"id": node_id, "route_id": r["id"], "kind": "resource", "label": out["src"],
                          "detail": out["semantic"] + " / " + out["layout"], "evidence": out["evidence"]})
            edges.append({"from": previous, "to": node_id, "kind": "conditional_resource_binding",
                          "evidence": out["evidence"], "runtime_observation": "not_collected"})
    return {
        "schema": SCHEMA, "edition": "E1.4a", "result": "source_only_binding_audit",
        "safety": {"hardware_accessed": False, "firmware_changed": False, "runtime_observed": False,
                   "device_playback_verified": False, "automatic_cleanup": False},
        "source": {"official_commit": COMMIT, "repository": "https://gitee.com/esp-friends/esp-vocat",
                   "current_comparison": "O2f2 archived source; file-level comparison, not a semantic equivalence proof",
                   "inventory_scope": scope, "inventory_file_count": len(source_paths),
                   "analysis_method": "lexical calls plus reviewed exact-anchor routes; not compiler reachability",
                   "inputs": sorted(ev.inputs.values(), key=lambda x: x["path"])},
        "summary": {"emotion_semantics": len(manifest), "unique_eaf_files": len(shared),
                    "action_map_count": len(emotion_actions), "routes": len(routes), "calls": len(calls),
                    "missing_semantics": missing, "shared_asset_count": sum(len(x) > 1 for x in shared.values()),
                    "shared_asset_semantics": {name: ids for name, ids in shared.items() if len(ids) > 1}},
        "layouts": {x["name"]: x for x in layout if x["type"] == "anim"},
        "semantics": semantics, "eaf_assets": eaf_assets, "actions": actions, "emotion_action_map": emotion_actions,
        "routes": routes, "shared_assets": shared, "call_inventory": calls,
        "evidence": ev.items, "graph": {"nodes": nodes, "edges": edges},
        "limitations": [
            "No runtime traces collected. Source presence is not a playback or physical completion fact.",
            "Dynamic server emotion and MCP decisions cannot be fully enumerated locally.",
            "No literal caller in the audited scope does not mean unused or safe to remove.",
            "O2f2 source comparison is file-level only; official graph is not a full current-firmware graph.",
            "Preview reuses validated host atlases; full target renderer/timing/clipping remains E1.3d/E2.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=REPO)
    parser.add_argument("--current-source", type=Path, default=CURRENT)
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/expression-bindings-e1-4a-20261021/bindings.json")
    args = parser.parse_args()
    try:
        report = build(args.repo, args.current_source)
        destination = args.output.resolve()
        destination.relative_to(ROOT.resolve())
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"result": "rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"schema": SCHEMA, **report["summary"], "safety": report["safety"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
