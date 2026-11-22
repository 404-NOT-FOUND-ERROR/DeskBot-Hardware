"""Project saved evidence into an operator's field capture checklist."""
from copy import deepcopy

from protocol.shell_repeatability import TARGET_MOUNTS

SCHEMA = 'deskbot.shell_field_guide/v1'
SETUP_SCHEMA = 'deskbot.shell_field_setup/v1'


def validate_setup(value):
    if (not isinstance(value, dict)
            or set(value) != {'schema', 'layout_fixed', 'frame_confirmed', 'unknown_reference'}
            or value['schema'] != SETUP_SCHEMA):
        raise ValueError('外壳采样准备项格式无效')
    if type(value['layout_fixed']) is not bool or type(value['frame_confirmed']) is not bool:
        raise ValueError('安装确认项应为勾选值')
    note = value['unknown_reference']
    if (not isinstance(note, str) or len(note) > 80 or note != note.strip()
            or any(ord(c) < 32 for c in note)):
        raise ValueError('未知布局名称应为 80 字以内的完整文字')
    return deepcopy(value)


def empty_setup():
    return dict(schema=SETUP_SCHEMA, layout_fixed=False,
                frame_confirmed=False, unknown_reference='')


def build_field_guide(session, graph):
    """Read existing tasks in field order, preserving every saved task ID.

    This projection never records human acceptance. Completed counts alone
    cannot establish successful recognition or a confirmed role assignment.
    """
    common = dict(schema=SCHEMA, project_id=session['id'], revision=session['revision'],
                  device_id=session['device_id'], context=deepcopy(session['context']),
                  formal=session['source'] == 'field_project',
                  device_written=False)
    if session['source'] == 'historical_rehearsal':
        return dict(common, stage='historical_rehearsal', prepared=False,
                    next_action='历史演练：正式采样请新建项目', setup=None,
                    checks=[], groups=[], next_task=None, tasks=[], completion=None)
    if session['source'] != 'field_project':
        raise ValueError('采集项目来源无效')
    setup = validate_setup(session.get('field_setup', empty_setup()))
    reference_locked = any(t['scenario'] == 'unknown_layout' and t['recording'] is not None
                           for t in session['tasks'])
    shells = {s['shell_id']: s for s in session['shells']}
    checks = [dict(id='layout_fixed', label='每个外壳的磁铁布局已固定', done=setup['layout_fixed']),
              dict(id='frame_confirmed', label='本体、底座的安装方向已确认', done=setup['frame_confirmed']),
              dict(id='unknown_reference', label='未登记布局对照已准备',
                   done=bool(setup['unknown_reference']) and setup['unknown_reference'].casefold()
                   not in {s['name'].casefold() for s in shells.values()})]
    prepared = all(c['done'] for c in checks)
    groups, ordered = [], []
    for scenario, label, target in [('registration', '登记', len(shells)),
                                    ('remount', '重复装回', len(shells) * TARGET_MOUNTS),
                                    ('removed', '取下', TARGET_MOUNTS),
                                    ('unknown_layout', '未知布局', TARGET_MOUNTS)]:
        tasks = [t for t in session['tasks'] if t['scenario'] == scenario]
        done = sum(t['recording'] is not None for t in tasks)
        groups.append(dict(id=scenario, label=label, done=done, total=target,
                           complete=len(tasks) == target and done == target))
        for task in tasks:
            name = shells[task['shell_id']]['name'] if task['shell_id'] else label
            if scenario == 'registration':
                steps = [f'装好“{name}”，保持已确认的安装方向。']
            elif scenario == 'remount':
                steps = [f'将“{name}”完整取下，再重新装回，形成一次独立装配。']
            elif scenario == 'removed':
                steps = ['先装回一个已登记外壳，再取下，形成一次独立取下状态。']
            else:
                steps = [f'重新装好未登记布局“{setup["unknown_reference"] or "待选择"}”，与已登记外壳分开。']
            steps += ['停稳后告诉开发者，保持静止至少 10 秒完成采样。',
                      '开发者保存并导入本次日志，再开始下一项。']
            ordered.append(dict(task_id=task['id'], scenario=scenario, number=task['number'],
                                label=f'{name} · {label} {task["number"]:02d}',
                                completed=task['recording'] is not None,
                                suggested_mount_id='mount-' + task['id'], instructions=steps))
    next_task = next((t for t in ordered if not t['completed']), None)
    capture_complete = bool(groups) and all(g['complete'] for g in groups)
    assessment = session.get('assessment')
    report = assessment['report'] if assessment else {}
    evaluated = bool(assessment)
    # Empty groups, incomplete coverage and a single incorrect/unconfirmed
    # mount each keep the project in review, even if all files are present.
    evaluation_matches = (capture_complete and report.get('coverage_complete') is True
                          and report.get('all_evaluated_mounts_matched') is True)
    archive_sha = assessment['archive']['payload_sha256'] if assessment else None
    assignments = session['assignments']
    bindings_current = bool(shells) and all(
        len(rows := [a for a in assignments if a['shell_id'] == shell_id]) == 1
        and rows[0]['device_id'] == session['device_id']
        and rows[0]['magnet_profile_id'] == shell['magnet_profile_id']
        and rows[0]['archive_sha256'] == archive_sha
        and rows[0]['graph_sha256'] == graph['sha256']
        and rows[0]['role_id'] in graph['roles']
        for shell_id, shell in shells.items()) and evaluated
    ready = prepared and evaluation_matches and bindings_current
    if not prepared:
        stage, action = 'preparation', '先保存采样前检查'
    elif next_task:
        stage, action = next_task['scenario'], next_task['label']
    elif not evaluated:
        stage, action = 'assessment', '采样已收齐，评估并保存识别档案'
    elif not evaluation_matches:
        stage, action = 'review_samples', '查看识别结果，处理未匹配或未确认的样本'
    elif not bindings_current:
        stage, action = 'binding', '为每个外壳选择当前角色包中的角色'
    else:
        stage, action = 'ready_for_confirmation', '数据与绑定草稿已准备好，下一步确认绑定'
    return dict(common, stage=stage, prepared=prepared, next_action=action,
                setup=setup, reference_locked=reference_locked,
                checks=checks, groups=groups, next_task=next_task, tasks=ordered,
                completion=dict(capture_complete=capture_complete, evaluated=evaluated,
                                evaluation_matches=evaluation_matches, bindings_current=bindings_current,
                                ready_for_confirmation=ready, manual_acceptance=session['manual_acceptance']))
