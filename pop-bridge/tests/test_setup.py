"""Offline setup tests: synthetic snapshots confined to the private test tree."""
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_full_mail import W  # Installs tested DNS/Internet prohibition.
import account_state as S
import bridge_config as C
import setup_bridge as U


class SetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='setup-', dir=W/'records')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.destination = self.root/'output'
        self.source = self.root/'snapshot.dat'
        S.new_file(self.source, b'u2\nu1\n')
        self.rows = [dict(name='reader', agent_uid=41, host='pop.example.invalid',
                          port=995, username='user@example.invalid', listen_port=21110)]
        self.histories = [[self.source]]

    def plan(self):
        return U.prepare(self.destination, self.rows, self.histories)

    def answers(self, mode='import', confirm='CREATE OFFLINE SETUP'):
        prefix = [str(self.destination), '1', 'reader', '41', 'pop.example.invalid',
                  '', 'user@example.invalid', '21110', mode]
        history = (['1', str(self.source), 'IMPORT COMPLETE HISTORY'] if mode == 'import'
                   else ['START WITHOUT HISTORY'])
        return iter(prefix+history+[confirm])

    def invoke(self, answers):
        output = []
        # Root test runner may exercise synthetic fixtures; public CLI still refuses root.
        with patch.object(U, 'check_user'):
            code = U.main([], ask=lambda _: next(answers), say=output.append)
        return code, '\n'.join(output)

    def test_plan_is_read_only_and_merges_history(self):
        second = self.root/'ledger.dat'
        S.new_file(second, b'u3\nu2\n')
        self.histories[0].append(second)
        plan = self.plan()
        self.assertFalse(self.destination.exists())
        self.assertEqual(plan.seeds, (b'u1\nu2\nu3\n',))
        self.assertEqual(self.source.read_bytes(), b'u2\nu1\n')
        path = U.create(plan)
        config = C.load(path)
        self.assertEqual(config, plan.config)
        with S.locked(config, config.accounts[0]) as state:
            self.assertEqual(state.known.read_bytes(), plan.seeds[0])
        for path in self.destination.rglob('*'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)

    def test_two_accounts_and_explicit_no_history(self):
        self.rows.append(dict(self.rows[0], name='second', agent_uid=77,
                              username='second@example.invalid', listen_port=21111))
        self.histories.append(None)
        plan = self.plan()
        U.create(plan)
        for account, expected in zip(plan.config.accounts, (b'u1\nu2\n', b'')):
            with S.locked(plan.config, account) as state:
                self.assertEqual(state.known.read_bytes(), expected)

    def test_existing_destination_and_late_creation_refused(self):
        plan = self.plan()
        self.destination.mkdir()
        sentinel = self.destination/'keep'
        sentinel.write_bytes(b'unchanged')
        for action in (self.plan, lambda: U.create(plan)):
            with self.assertRaises(S.StateError): action()
        self.assertEqual(sentinel.read_bytes(), b'unchanged')
        self.assertEqual(list(self.destination.iterdir()), [sentinel])

    def test_invalid_duplicate_or_secret_config_rejected_before_writes(self):
        original = copy.deepcopy(self.rows)
        for key, value in (('password', 'DO-NOT-LOG'), ('name', '../escape'), ('port', 0)):
            self.rows = copy.deepcopy(original)
            self.rows[0][key] = value
            with self.assertRaises((C.ConfigError, S.StateError)): self.plan()
            self.assertFalse(self.destination.exists())
        self.rows = original*2
        self.histories *= 2
        with self.assertRaises(C.ConfigError): self.plan()

    def test_missing_empty_or_malformed_history_rejected(self):
        for data in (b'', b'not a UID\n'):
            self.source.write_bytes(data)
            with self.assertRaises(S.StateError): self.plan()
            self.assertFalse(self.destination.exists())
        self.histories = [[self.root/'missing']]
        with self.assertRaises(OSError): self.plan()
        self.assertFalse(self.destination.exists())

    def test_history_permission_and_combined_size_limit(self):
        self.source.chmod(0o644)
        with self.assertRaises(S.StateError): self.plan()
        self.source.chmod(0o600)
        self.histories[0].append(self.source)
        with patch.object(S, 'MAX_STATE_BYTES', 10):
            with self.assertRaisesRegex(S.StateError, 'state_too_large'): self.plan()

    def test_parent_permissions_and_destination_symlink_refused(self):
        self.root.chmod(0o777)
        with self.assertRaises(S.StateError): self.plan()
        self.root.chmod(0o700)
        self.destination.symlink_to(self.root/'missing')
        with self.assertRaises(S.StateError): self.plan()

    def test_mid_creation_failure_keeps_partial_state_without_configuration(self):
        plan = self.plan()
        with patch.object(S, 'initialize', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): U.create(plan)
        self.assertTrue(self.destination.is_dir())
        self.assertFalse((self.destination/'config.json').exists())
        with self.assertRaises(S.StateError): U.create(plan)
        self.assertEqual(self.source.read_bytes(), b'u2\nu1\n')

    def test_interactive_import_success(self):
        code, output = self.invoke(self.answers())
        self.assertEqual(code, 0, output)
        self.assertIn('OFFLINE SETUP CREATED', output)
        self.assertNotIn('SETUP STOP', output)
        self.assertNotIn('u1', output)
        self.assertEqual(len(C.load(self.destination/'config.json').accounts), 1)

    def test_new_history_requires_its_own_confirmation(self):
        answers = list(self.answers(mode='new'))
        answers[-2] = 'no'
        code, output = self.invoke(iter(answers))
        self.assertEqual(code, 1)
        self.assertIn('no_history_not_confirmed', output)
        self.assertFalse(self.destination.exists())
        code, output = self.invoke(self.answers(mode='new'))
        self.assertEqual(code, 0, output)

    def test_final_cancellation_and_eof_leave_no_destination(self):
        code, output = self.invoke(self.answers(confirm='no'))
        self.assertEqual(code, 1)
        self.assertNotIn('OFFLINE SETUP CREATED', output)
        self.assertFalse(self.destination.exists())
        output = []
        with patch.object(U, 'check_user'):
            code = U.main([], ask=lambda _: (_ for _ in ()).throw(EOFError()), say=output.append)
        self.assertEqual(code, 1)
        self.assertFalse(self.destination.exists())

    def test_main_failure_cannot_be_reported_as_success(self):
        with patch.object(U, 'create', side_effect=OSError('DO-NOT-LOG')):
            code, output = self.invoke(self.answers())
        self.assertEqual(code, 1)
        self.assertIn('SETUP STOP', output)
        self.assertNotIn('OFFLINE SETUP CREATED', output)
        self.assertNotIn('DO-NOT-LOG', output)

    def test_root_and_sudo_refused_help_is_harmless(self):
        for real, effective in ((0, 0), (1000, 0), (1000, 1001)):
            with patch.object(U.os, 'getuid', return_value=real), patch.object(
                    U.os, 'geteuid', return_value=effective):
                with self.assertRaises(S.StateError): U.check_user()
        with patch.object(U.os, 'getuid', return_value=1000), patch.object(
                U.os, 'geteuid', return_value=1000): U.check_user()
        with patch('sys.stdout', new_callable=io.StringIO), self.assertRaises(SystemExit) as exit_code:
            U.main(['--help'])
        self.assertEqual(exit_code.exception.code, 0)
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
