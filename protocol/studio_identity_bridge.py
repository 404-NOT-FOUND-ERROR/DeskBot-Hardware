"""Single-owner USB bridge with E3b hello projection."""
import json
import jsonschema
from referencing import Registry, Resource

from protocol.studio_body_bridge import ConsoleBridge, ROOT
from protocol.studio_identity_observation import IdentityObservation, SCHEMA, aged_identity_snapshot


class IdentityBridge(ConsoleBridge):
    def __init__(self, python=None, **kwargs):
        schema = json.loads((ROOT / 'protocol/deskbot-body-observation-v2.schema.json').read_bytes())
        hello = json.loads((ROOT / 'protocol/deskbot-device-link-v1.schema.json').read_bytes())
        registry = Registry().with_resource(hello['$id'], Resource.from_contents(hello))
        validator = jsonschema.Draft202012Validator(schema, registry=registry,
                                                   format_checker=jsonschema.FormatChecker())
        super().__init__(python, worker=ROOT / "tools/observe-studio-body-e3b.py",
                         observation_factory=IdentityObservation, snapshot_schema=SCHEMA,
                         age_snapshot=aged_identity_snapshot, validate_snapshot=validator.is_valid, **kwargs)
