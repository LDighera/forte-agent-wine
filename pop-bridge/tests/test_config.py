"""Configuration-only tests: fictitious values, no profile reads or connections."""
import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bridge_config as C


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT/'config.example.json').read_text())

    def parse(self):
        return C.parse(json.dumps(self.data))

    def test_example_two_arbitrary_account_ids(self):
        result = self.parse()
        self.assertEqual([a.agent_uid for a in result.accounts], [41, 77])
        self.assertNotEqual(result.account_state(result.accounts[0]),
                            result.account_state(result.accounts[1]))

    def test_one_account_allowed_without_four_account_requirement(self):
        self.data['accounts'] = self.data['accounts'][:1]
        self.assertEqual(len(self.parse().accounts), 1)

    def test_rejects_duplicate_mappings(self):
        original = copy.deepcopy(self.data)
        for key in ('name', 'agent_uid', 'listen_port', 'known_uids_file'):
            self.data = copy.deepcopy(original)
            self.data['accounts'][1][key] = self.data['accounts'][0][key]
            with self.subTest(key=key), self.assertRaisesRegex(C.ConfigError, 'duplicate_account_mapping'):
                self.parse()
        self.data = copy.deepcopy(original)
        self.data['accounts'][1].update({k: self.data['accounts'][0][k]
                                        for k in ('host', 'port', 'username')})
        with self.assertRaises(C.ConfigError): self.parse()

    def test_does_not_accept_secrets_or_safety_overrides(self):
        for key, value in (('password', 'private-marker'), ('verify_tls', False),
                           ('allow_delete', True), ('listen_host', '0.0.0.0')):
            with self.subTest(key=key):
                data = copy.deepcopy(self.data)
                data['accounts'][0][key] = value
                with self.assertRaises(C.ConfigError) as error:
                    C.parse(json.dumps(data))
                self.assertNotIn('private-marker', str(error.exception))

    def test_invalid_types_and_protocol_injection(self):
        for key, value in (('agent_uid', True), ('port', False), ('port', 0),
                           ('listen_port', 110), ('username', 'user\r\nDELE 1'),
                           ('host', 'https://example.invalid'), ('name', '../escape'),
                           ('known_uids_file', 'relative.dat')):
            data = copy.deepcopy(self.data); data['accounts'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(C.ConfigError):
                C.parse(json.dumps(data))

    def test_duplicate_json_keys_and_unknown_root_fields(self):
        with self.assertRaisesRegex(C.ConfigError, 'duplicate_key'):
            C.parse('{"version":1,"version":1}')
        self.data['unexpected'] = 'private-marker'
        with self.assertRaisesRegex(C.ConfigError, 'config_fields_invalid'):
            self.parse()

    def test_empty_accounts_invalid_version_and_oversized_input(self):
        for key, value in (('accounts', []), ('version', True), ('version', 2),
                           ('state_root', '/'), ('state_root', '/tmp/../private')):
            data = copy.deepcopy(self.data); data[key] = value
            with self.subTest(key=key), self.assertRaises(C.ConfigError):
                C.parse(json.dumps(data))
        with self.assertRaisesRegex(C.ConfigError, 'config_size_invalid'):
            C.parse(' ' * 65537)


if __name__ == '__main__':
    unittest.main()
