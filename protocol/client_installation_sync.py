"""Transfer only the current saved installation; this module never controls USB."""
from copy import deepcopy
import hashlib
from http.client import HTTPException
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, Request

from protocol.studio_shared_owner import NoRedirect
from protocol.studio_usb_session import _unique
from protocol.client_presentation_ledger import canonical

SCHEMA = 'deskbot.owner-installation-handoff.v1'
MAX_BODY = 128 * 1024


class InstallationHTTP:
    def __init__(self, url, timeout=5):
        parts = urlsplit(url)
        if (parts.scheme != 'http' or parts.hostname != '127.0.0.1' or not parts.port
                or parts.username or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError('请指定明确的本机服务地址')
        self.url, self.timeout = url.rstrip('/'), timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, method, route, value=None):
        raw = None if value is None else json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if raw is not None and len(raw) > MAX_BODY:
            raise ValueError('安装记录超过 128 KiB，请先归档')
        request = Request(self.url + route, data=raw, method=method,
            headers={'Content-Type': 'application/json', 'X-DeskBot-Integration': 'shell-installation-v1'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                result = response.read(2 * 1024 * 1024 + 1)
                if len(result) > 2 * 1024 * 1024:
                    raise ValueError('安装状态超过大小限制')
                length = response.headers.get('Content-Length')
                if length is not None and int(length) != len(result):
                    raise OSError('安装状态响应中断，请重试原安装记录')
                value = json.loads(result, object_pairs_hook=_unique)
                if type(value) is not dict:
                    raise ValueError('安装接口须返回完整状态对象')
                return value
        except HTTPError as error:
            try:
                detail = error.read(4096).decode('utf-8', errors='replace')
            except HTTPException:
                detail = '响应中断'
            raise OSError(f'安装同步 HTTP {error.code}: {detail}') from error
        except HTTPException as error:
            raise OSError('安装同步连接中断，请重试原安装记录') from error


def prepare(state, *, source_id, character_id, device_id):
    if (type(state) is not dict or state.get('character_id') != character_id or state.get('scope') != 'device_expression'
            or state.get('error') or type(state.get('revision')) is not int):
        raise ValueError('请先核对安装服务与持续个体')
    row = next((r for r in state.get('records', []) if r.get('installation_id') == state.get('current')), None)
    if not row or row.get('phase') != 'saved':
        return None
    if row.get('character_id') != character_id or row.get('device_id') != device_id:
        raise ValueError('当前安装属于其他个体或设备')
    device = state.get('device')
    if not device:
        return None
    expression = row['profile']['expression_role_id']
    if (device.get('device_id') != device_id or device.get('graph_sha256') != row['profile']['expression_graph_sha256']
            or any(device.get(key) != expression for key in ('current', 'actual', 'target', 'saved'))
            or device.get('save_state') not in ('saved', 'restored')
            or device.get('activation', {}).get('status') in ('queued', 'switching')):
        return None
    return dict(schema=SCHEMA, source_id=source_id, source_revision=state['revision'],
                installation=deepcopy(row), device=deepcopy(device))


def sync_once(workbench, client, *, source_id, character_id, device_id):
    state = workbench.request('GET', '/api/shell-installation/state')
    value = prepare(state, source_id=source_id, character_id=character_id, device_id=device_id)
    if value is None:
        return dict(status='waiting', reason='等待当前安装与设备保存结果')
    result = client.request('POST', '/api/hardware/installations', value)
    return validate_result(value, result)


def validate_result(value, result):
    """Use the same receipt rules for one-shot transfer and the runtime worker."""
    character_id = value['installation']['character_id']
    device_id, source_id = value['installation']['device_id'], value['source_id']
    if type(result) is not dict or type(result.get('receipt')) is not dict:
        raise ValueError('客户端接收凭证不完整')
    receipt = result['receipt']
    if (result.get('accepted') is not True or receipt.get('installation_id') != value['installation']['installation_id']
            or receipt.get('character_id') != character_id or receipt.get('device_id') != device_id
            or receipt.get('source_id') != source_id
            or receipt.get('installation_sha256') != hashlib.sha256(canonical(value['installation']).encode()).hexdigest()
            or receipt.get('scope') != 'installation_facts' or receipt.get('status') != 'recorded'):
        raise ValueError('客户端接收凭证与本次安装不符')
    return dict(status='synchronized' if result.get('current_installation_id') == receipt['installation_id'] else 'superseded',
                result=result)
