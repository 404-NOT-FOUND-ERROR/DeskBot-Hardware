"""In-memory sole owner driving the production ControlLink and receipt codec."""
from copy import deepcopy
import json
import threading

from protocol import expression_control
from protocol.presentation_coordinator import PresentationCoordinator
from protocol.studio_control_link import validate_command
from tests.test_studio_control_link import LinkTests, DEVICE, BOOT, SESSION
from tests.test_studio_device_roles_e3f9d import reply


class MemoryPresentationOwner:
    def __init__(self):
        case = LinkTests(); case.setUp(); case.ready()
        self.link, self.at = case.link, 410
        self.link.client._cached['value']['expression_semantics'] = ['neutral', 'happy']
        self.link.roles = dict(status='current', reply=reply() | dict(active_index=0, target_index=0,
            current_index=0, sleep='awake'), age_ms=0, pending=None, error=None)
        self.link.role_received = self.at
        self.lock = threading.RLock()
        self.presentation = PresentationCoordinator(self)
        self.sent = []
        self.auto_receipt = True
        self.raise_after_send = False

    def control(self):
        row = self.link.snapshot(self.at)
        row['host_command'] = None
        return row

    def command(self, value):
        return self.presentation.submit(value)

    def _command(self, value, *, before_write=None):
        validate_command(value)
        if not self.control()['can_submit']:
            raise ValueError('not_ready')
        if before_write:
            before_write()
        self.sent.append(deepcopy(value))
        self.link.command(value, self.at)
        self.at += 400
        wire = self.link.tick(self.at)
        if value['operation'] in ('neutral','happy'):
            request=json.loads(wire)
            expression_control.validate_request(request)
            if self.auto_receipt:
                self.receipt('accepted'); self.receipt('dispatched')
        self.presentation.observe(self.control())
        if self.raise_after_send:
            raise OSError('lost_host_return')
        return self.control()

    def receipt(self, status):
        request = self.link.history[-1]
        self.at += 10
        value=dict(schema=expression_control.SCHEMA,message_type='receipt',target_id=DEVICE,boot_id=BOOT,
            session_id=SESSION,sequence=self.link.receipt_sequence+1,request_id=request['request_id'],
            request_sequence=request['sequence'],status=status,reason='submitted_to_display' if status=='dispatched' else 'queued_for_owner',
            source='device',correlated=True,original_status=None,at_ms=self.at)
        self.link.accept(value,self.at)
        self.presentation.observe(self.control())
        return value

    def busy(self, value=True):
        self.link.client._cached['value']['page']['value'] = 'other' if value else 'home'
        self.link.client._cached['value']['preview']['reason'] = 'not_home' if value else 'sleep_unknown'

    def restart(self):
        self.presentation = PresentationCoordinator(self)
