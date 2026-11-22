"""Full-role semantics cross the production USB parser and share the display schema."""
import json
import unittest
from tests import test_studio_expression_control as existing
from protocol.studio_display_query import EXPRESSION_SEMANTICS


class FullRoleUSBTests(unittest.TestCase):
    SHY_ENABLED=EAT_ENABLED=COMMON_ENABLED=ROLE_PLAYER_ENABLED=True
    request=existing.ExpressionControlTests.request
    wire=existing.ExpressionControlTests.wire
    decode=existing.ExpressionControlTests.decode

    @classmethod
    def setUpClass(cls):existing.ExpressionControlTests.setUpClass.__func__(cls)

    def test_all_29_production_parser(self):
        for semantic in EXPRESSION_SEMANTICS:
            with self.subTest(semantic=semantic):
                self.assertEqual(self.decode(self.wire(command=dict(kind='play_expression',semantic_id=semantic)).strip()).returncode,0)

    def test_schema_and_manifest_match_29(self):
        field=json.loads((existing.ROOT/'protocol/deskbot-studio-display-v1.schema.json').read_bytes())['properties']['expression_semantics']
        manifest=json.loads((existing.ROOT/'artifacts/airboat-role-20261121/manifest.json').read_bytes())
        self.assertEqual(field['maxItems'],29)
        self.assertEqual(set(field['items']['enum']),set(EXPRESSION_SEMANTICS))
        self.assertEqual(set(EXPRESSION_SEMANTICS),set(manifest['expressions']))


if __name__=='__main__':unittest.main()
