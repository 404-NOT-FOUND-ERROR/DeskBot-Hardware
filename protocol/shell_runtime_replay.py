"""Original captured windows through M2 and a memory role device."""
from copy import deepcopy
from datetime import datetime
import json

from protocol.magnetic_stream import MagneticStream
from protocol.shell_switch_runtime import ShellSwitchRuntime
from protocol.shell_switch_lab import SessionLab, DEVICE
from protocol.shell_profile_archive import canonical
from protocol.studio_usb_session import DEVICE as DEVICE_PATTERN

MAX_REPLAY_TICKS = 6000
MAX_REPLAY_SAMPLES = 20000


def _points(window):
    samples = window['samples']
    if not samples or len(samples) > MAX_REPLAY_SAMPLES:
        raise ValueError('请选择含有效样本的分段日志')
    origin = datetime.fromisoformat(samples[0].host_received_at)
    points = [(2000+round((datetime.fromisoformat(s.host_received_at)-origin).total_seconds()*1000),s)
              for s in samples]
    if points[0][0] != 2000 or any(b[0] <= a[0] for a,b in zip(points,points[1:])):
        raise ValueError('日志样本需要递增接收时间')
    # Check the time budget before allocating a potentially huge range/set.
    ticks = (points[-1][0]+5099)//100+1+len(points)
    if ticks > MAX_REPLAY_TICKS:
        raise ValueError('单个日志窗口过长，请分段重放')
    return points, ticks


def replay_recordings(capture, bindings, graph):
    device = capture['device_id']
    valid_device = type(device) is str and DEVICE_PATTERN.fullmatch(device)
    # Historical fixture labels remain review-only; use a marked memory identity for protocol bootstrapping.
    effective_device = device if valid_device else DEVICE
    active = deepcopy(bindings)
    if not valid_device:
        for binding in active.values():
            binding['ready'] = False
    rows = []
    total_samples = 0
    windows = [(window,*_points(window)) for window in capture['recordings']]
    if sum(ticks for _,_,ticks in windows) > MAX_REPLAY_TICKS:
        raise ValueError('日志联测总时长较长，请选择更短的分段项目')
    for window,points,_ in windows:
        lab = SessionLab(active, graph, device_id=effective_device)
        runtime = ShellSwitchRuntime(active,device_id=effective_device,
                                     archive=capture['archive'],context=capture['context'])
        stream = MagneticStream()
        next_sample = 0
        input_recognition = runtime.snapshot()['recognition']
        last_known = None
        schedule = sorted(set(range(0,points[-1][0]+5100,100)) | {p[0] for p in points})
        for at in schedule:
            control = lab.pump(at)
            while next_sample < len(points) and points[next_sample][0] == at:
                sample = points[next_sample][1]
                payload = bytes.fromhex(sample.payload_hex)
                line = ('Handle: cmd=06, '+payload[1:].hex(' ')).encode('ascii')
                stream.line(line,control,at)
                next_sample += 1
            runtime.update(control,stream.snapshot(control),at)
            if at <= points[-1][0]:
                input_recognition = runtime.snapshot()['recognition']
                if input_recognition['status'] == 'known':
                    last_known = input_recognition['profile_id']
            command = runtime.next_command(at)
            if command:
                lab.command(command['operation'],at,command)
        final = runtime.snapshot()
        adapter = final['adapter']
        profile = input_recognition.get('profile_id') if input_recognition['status']=='known' else None
        matches = [b for b in active.values() if b.get('source')
            and b['source']['archive_sha256'] == capture['archive']['payload_sha256']
            and b['source']['magnet_profile_id'] == profile and b['source']['device_id'] == device]
        binding = matches[0] if len(matches)==1 else None
        activations = [c for c in lab.commands if c['operation'].startswith('role.activate.')]
        rows.append(dict(recording_id=window['id'],scenario=window.get('scenario'),sample_count=len(points),
            recognition=input_recognition,tail_recognition=final['recognition'],last_known_profile=last_known,
            input_final_profile=profile,shell_name=binding and binding['shell_name'],
            profile_label=next((p['label'] for p in capture['archive']['profiles'] if p['profile_id']==profile),None),
            role_name=binding and binding['role_name'],
            state=adapter['decision'],timeline=adapter['timeline'],simulated_activation_count=len(activations),
            simulated_wire_requests=len(lab.device.wires),error=adapter['error'],
            adapter_reason=adapter['reason'],registration_ready=bool(binding and binding.get('ready'))))
        total_samples += len(points)
    result = dict(kind='runtime_replay',input_kind='stored_samples_and_memory_device',
                segment_mode='independent_recording_windows',rows=rows,total_samples=total_samples,
                archive_sha256=capture['archive']['payload_sha256'],session_id=capture['session_id'],
                simulated_activation_count=sum(r['simulated_activation_count'] for r in rows),
                device_commands=0,hardware_accessed=False,voice_service='pending')
    # Keep the immediate HTTP result identical to the saved/reopened JSON representation.
    return json.loads(canonical(result))
