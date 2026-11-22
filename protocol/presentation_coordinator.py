"""One presentation authority under the original USB owner's reentrant lock."""
from copy import deepcopy
import uuid

from protocol.studio_display_query import EXPRESSION_SEMANTICS


class PresentationError(ValueError):
    pass


class PresentationCoordinator:
    def __init__(self, bridge):
        self.bridge = bridge
        self.owner_epoch = uuid.uuid4().hex
        self.ledger = None
        self.version = dict(generation=0, fingerprint=None)
        self.observers = []

    def attach(self, ledger):
        with self.bridge.lock:
            if self.ledger is not None and self.ledger is not ledger:
                raise PresentationError('coordinator_already_attached')
            self.ledger = ledger
            saved = ledger.get('meta', 'presentation')
            if saved and saved['generation'] >= self.version['generation']:
                self.version = saved

    def detach(self, ledger):
        with self.bridge.lock:
            if self.ledger is ledger:
                self.ledger = None

    def _save(self, value):
        if self.ledger:
            self.ledger.put('meta', 'presentation', value)
        self.version = value

    def advance(self):
        self._save(dict(self.version, generation=self.version['generation'] + 1))

    @staticmethod
    def facts(control):
        roles = control.get('roles') or {}
        reply = roles.get('reply') or {}
        ids = ('target_id', 'boot_id', 'session_id')
        if (roles.get('status') != 'current' or not isinstance(roles.get('age_ms'), int)
                or roles['age_ms'] >= 3000 or not reply.get('loaded')
                or any(not control.get(k) or reply.get(k) != control[k] for k in ids)):
            raise PresentationError('binding_unavailable')
        index = reply.get('active_index', -1)
        if (type(index) is not int or not 0 <= index < len(reply.get('roles', []))
                or reply.get('target_index') != index or reply.get('current_index') != index
                or (reply.get('activation') or {}).get('status') in ('queued', 'switching')):
            raise PresentationError('role_switching')
        return dict(device_id=control['target_id'], role_id=reply['roles'][index],
                    graph_sha256=reply['graph_sha256'])

    def observe(self, control):
        # Called on every worker snapshot, including the first terminal receipt.
        with self.bridge.lock:
            try:
                facts = self.facts(control)
            except PresentationError:
                facts = None
            if facts and facts != self.version['fingerprint']:
                generation = self.version['generation'] + (self.version['fingerprint'] is not None)
                self._save(dict(generation=generation, fingerprint=facts))
            for observer in self.observers:
                observer(control)

    def snapshot(self):
        with self.bridge.lock:
            control = self.bridge.control()
            self.observe(control)
            return dict(self.facts(control), generation=self.version['generation'])

    def validate(self, binding):
        current = self.snapshot()
        if any(binding.get(k) != current[k] for k in current):
            raise PresentationError('superseded')
        return current

    def submit(self, value, *, expected=None, prepare=None):
        with self.bridge.lock:
            if expected is not None:
                self.validate(expected)

            def before_write():
                # Owner validation and ledger preparation share the same lock as
                # workbench/shell commands. No role switch can enter this gap.
                if expected is not None:
                    self.validate(expected)
                if prepare:
                    prepare()
                if expected is None and (value['operation'] in EXPRESSION_SEMANTICS
                        or value['operation'].startswith('role.activate.') or value['operation'] == 'disable'):
                    self.advance()

            return self.bridge._command(value, before_write=before_write)
