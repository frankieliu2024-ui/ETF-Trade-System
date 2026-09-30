import pathlib
import re
import unittest


class FormalReplayForensicReachabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = pathlib.Path('.github/workflows/market-snapshot.yml').read_text(encoding='utf-8')

    def test_dispatch_input_and_restricted_env_mapping_exist(self):
        self.assertRegex(self.workflow, r'^\s{6}formal_replay_forensic:\s*, re.MULTILINE)
        self.assertIn('default: "false"', self.workflow)
        self.assertIn('type: boolean', self.workflow)
        self.assertIn('ETF_FORMAL_REPLAY_FORENSIC:', self.workflow)
        self.assertIn("github.event.inputs.formal_replay_request != ''", self.workflow)
        self.assertIn("github.event.inputs.formal_replay_forensic == 'true'", self.workflow)
        self.assertIn("&& '1' || '0'", self.workflow)

    def test_illegal_combinations_fail_closed(self):
        self.assertIn('formal_replay_forensic=true requires formal_replay_request', self.workflow)
        self.assertIn('formal_replay_forensic must be true or false', self.workflow)
        self.assertIn("if: \\${{ github.event_name == 'workflow_dispatch' }}", self.workflow)

    def test_only_formal_replay_can_enable_forensic_env(self):
        mapping = "github.event_name == 'workflow_dispatch' && github.event.inputs.formal_replay_request != '' && github.event.inputs.formal_replay_forensic == 'true' && '1' || '0'"
        self.assertIn(mapping, self.workflow)
        self.assertNotIn('ETF_FORMAL_REPLAY_FORENSIC: ${{ github.event.inputs.formal_replay_forensic', self.workflow)
        self.assertIn("github.event.schedule || ''", self.workflow)

    def test_existing_instrumentation_logic_is_not_modified(self):
        code = pathlib.Path('scripts/process_state_sync_request.py').read_text(encoding='utf-8')
        self.assertIn('ETF_FORMAL_REPLAY_FORENSIC == "1"', code)
        self.assertIn('forensic_historical_replay=forensic_historical_replay', code)


if __name__ == '__main__':
    unittest.main()
, re.MULTILINE)
        self.assertIn('default: "false"', self.workflow)
        self.assertIn('type: boolean', self.workflow)
        self.assertIn('ETF_FORMAL_REPLAY_FORENSIC:', self.workflow)
        self.assertIn("github.event.inputs.formal_replay_request != ''", self.workflow)
        self.assertIn("github.event.inputs.formal_replay_forensic == 'true'", self.workflow)
        self.assertIn("&& '1' || '0'", self.workflow)

    def test_illegal_combinations_fail_closed(self):
        self.assertIn('formal_replay_forensic=true requires formal_replay_request', self.workflow)
        self.assertIn('formal_replay_forensic must be true or false', self.workflow)
        self.assertIn("if: \\${{ github.event_name == 'workflow_dispatch' }}", self.workflow)

    def test_only_formal_replay_can_enable_forensic_env(self):
        mapping = "github.event_name == 'workflow_dispatch' && github.event.inputs.formal_replay_request != '' && github.event.inputs.formal_replay_forensic == 'true' && '1' || '0'"
        self.assertIn(mapping, self.workflow)
        self.assertNotIn('ETF_FORMAL_REPLAY_FORENSIC: ${{ github.event.inputs.formal_replay_forensic', self.workflow)
        self.assertIn("github.event.schedule || ''", self.workflow)

    def test_existing_instrumentation_logic_is_not_modified(self):
        code = pathlib.Path('scripts/process_state_sync_request.py').read_text(encoding='utf-8')
        self.assertIn('ETF_FORMAL_REPLAY_FORENSIC == "1"', code)
        self.assertIn('forensic_historical_replay=forensic_historical_replay', code)


if __name__ == '__main__':
    unittest.main()
