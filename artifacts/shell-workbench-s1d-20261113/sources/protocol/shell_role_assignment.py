"""Resolve saved shell registrations into installed-expression-role previews."""
from copy import deepcopy

from protocol.shell_profile_archive import validate_archive
from protocol.studio_device_roles import validate_snapshot


def resolve_assignment(archive, assignments, observation, graph):
    """Pure projection: an offline observation yields an operation preview.

    A magnetic registration version is the archive payload digest. It changes
    when training or its provenance changes, so old assignments need review.
    Unknown, stale and incomplete identities keep the currently selected role.
    """
    archive = validate_archive(archive)
    empty = dict(action='keep_current', reason=observation['status'], shell_id=None,
                 role_id=None, operation=None, scope='offline_rehearsal')
    if observation['status'] != 'known':
        return empty
    profile = observation.get('profile_id')
    if profile not in {p['profile_id'] for p in archive['profiles']}:
        return dict(empty, reason='profile_not_registered')
    row = next((a for a in assignments if a['magnet_profile_id'] == profile), None)
    if row is None:
        return dict(empty, reason='shell_not_assigned')
    if row['archive_sha256'] != archive['payload_sha256']:
        return dict(empty, reason='registration_changed')
    if row['graph_sha256'] != graph['sha256']:
        return dict(empty, reason='role_package_changed')
    if row['role_id'] not in graph['roles']:
        return dict(empty, reason='role_not_installed')
    index = graph['roles'].index(row['role_id'])
    return dict(empty, action='preview_activation', reason='registered_shell',
                shell_id=row['shell_id'], role_id=row['role_id'],
                operation=f"role.activate.{index}.{graph['sha256']}")


def activation_preview(resolution, control, *, expected_device_id):
    """Recheck the live device/session and graph without sending a command."""
    result = dict(resolution=deepcopy(resolution), ready=False, reason='rehearsal_only',
                  command=None, device_activated=False)
    if resolution['operation'] is None:
        return result
    if (control.get('target_id') != expected_device_id or not control.get('enabled')
            or not control.get('session_id') or control.get('status') != 'ready'
            or control.get('pending') or control.get('host_command')):
        return dict(result, reason='live_session_required')
    identity = {k: control[k] for k in ('target_id', 'boot_id', 'session_id')}
    try:
        snapshot = validate_snapshot(control['roles'], identity)
    except (ValueError, KeyError, TypeError):
        return dict(result, reason='fresh_roles_required')
    row = snapshot['reply']
    index = int(resolution['operation'].split('.')[2])
    graph = resolution['operation'].split('.')[3]
    if row is None or row['graph_sha256'] != graph or row['roles'][index:index + 1] != [resolution['role_id']]:
        return dict(result, reason='role_package_changed')
    if row['blocked'] or row['activation']['status'] in ('queued', 'switching'):
        return dict(result, reason='player_busy')
    # This is a template. S2b creates a new command_id from fresh live evidence.
    return dict(result, ready=True, reason='interface_matches', command=dict(
        operation=resolution['operation'], **{k: control[k] for k in (
            'connection_id', 'target_id', 'boot_id', 'session_id')}))
