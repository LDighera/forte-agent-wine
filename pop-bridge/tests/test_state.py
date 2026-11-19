"""Private synthetic state and adapter tests; no production paths or mail."""
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Reuse fixtures AND the DNS/Internet prohibition from the existing tests.
from test_full_mail import W, Cycle, FakePOP, BODY
import account_state as S
import bridge_config as C
import runtime_adapter as R


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s-', dir=W/'records')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seed = self.root/'source.dat'
        S.new_file(self.seed, b'u1\n')
        self.state_root = self.root/'state'
        self.state_root.mkdir(mode=0o700)
        self.account = C.Account('reader', 41, 'controlled.invalid', 995,
                                 'user', 21110, self.seed)
        self.config = C.Config(self.state_root, (self.account,))
        self.target = self.config.account_state(self.account)

    def initialize(self, **kwargs):
        return S.initialize(self.config, self.account, **kwargs)

    def locked(self):
        return S.locked(self.config, self.account)

    def session(self):
        return R.session(self.config, self.account)

    def test_initialization_canonicalizes_without_changing_source(self):
        original = b'u2\r\nu1\r\nu2\r\n'
        self.seed.write_bytes(original)
        self.initialize()
        self.assertEqual(self.seed.read_bytes(), original)
        self.assertEqual((self.target/'known-uids.dat').read_bytes(), b'u1\nu2\n')
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        for path in self.target.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.locked() as state:
            self.assertEqual(state.delivered.read_bytes(), b'')
            self.assertEqual(state.deferred.read_bytes(), b'')

    def test_missing_and_empty_history_are_not_implicit_fresh_accounts(self):
        self.seed.rename(self.root/'held.dat')
        with self.assertRaisesRegex(S.StateError, 'initialization_io_failed'):
            self.initialize(confirm_empty_seed=True)
        self.assertFalse(self.target.exists())
        S.new_file(self.seed, b'')
        for flag in (False, 1, 'yes'):
            with self.assertRaisesRegex(S.StateError, 'empty_seed_needs_confirmation'):
                self.initialize(confirm_empty_seed=flag)
            self.assertFalse(self.target.exists())
        self.initialize(confirm_empty_seed=True)
        with self.locked(): pass

    def test_existing_state_is_never_overwritten(self):
        self.initialize()
        (self.target/'delivered.dat').write_bytes(b'u2\n')
        before = {p.name: p.read_bytes() for p in self.target.iterdir()}
        with self.assertRaisesRegex(S.StateError, 'account_state_exists'):
            self.initialize()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.target.iterdir()})

    def test_interrupted_initialization_is_not_repaired_implicitly(self):
        create = S.new_file
        def interrupted(path, data):
            if path.name == 'deferred.dat': raise OSError('synthetic write failure')
            create(path, data)
        with patch.object(S, 'new_file', interrupted):
            with self.assertRaises(S.StateError): self.initialize()
        self.assertTrue(self.target.exists())
        self.assertFalse((self.target/'ready.json').exists())
        with self.assertRaises(S.StateError), self.locked(): pass
        with self.assertRaisesRegex(S.StateError, 'account_state_exists'):
            self.initialize()

    def test_identity_changes_refused_but_local_port_can_change(self):
        self.initialize()
        for key, value in (('host', 'different.invalid'), ('port', 999),
                           ('username', 'different'), ('agent_uid', 77),
                           ('known_uids_file', self.root/'other.dat')):
            account = replace(self.account, **{key: value})
            config = replace(self.config, accounts=(account,))
            with self.subTest(key=key), self.assertRaisesRegex(
                    S.StateError, 'account_identity_changed'), S.locked(config, account):
                pass
        account = replace(self.account, listen_port=21111)
        config = replace(self.config, accounts=(account,))
        with S.locked(config, account): pass
        with self.locked(): pass  # Failed validation did not retain a lock.

    def test_exclusive_account_lock_and_release(self):
        self.initialize()
        with self.locked():
            with self.assertRaisesRegex(S.StateError, 'account_in_use'), self.locked(): pass
        with self.locked(): pass

    def test_wrong_permissions_and_seed_symlink_refused(self):
        self.seed.chmod(0o644)
        with self.assertRaisesRegex(S.StateError, 'file_not_private_regular'):
            self.initialize()
        self.seed.chmod(0o600)
        self.seed.rename(self.root/'real-seed.dat')
        self.seed.symlink_to(self.root/'real-seed.dat')
        with self.assertRaisesRegex(S.StateError, 'seed_path_invalid'): self.initialize()
        self.assertFalse(self.target.exists())

    def test_directory_symlink_and_permissive_root_refused(self):
        self.state_root.chmod(0o755)
        with self.assertRaisesRegex(S.StateError, 'directory_not_private'): self.initialize()
        self.state_root.chmod(0o700)
        self.state_root.rename(self.root/'real-state')
        self.state_root.symlink_to(self.root/'real-state', target_is_directory=True)
        with self.assertRaisesRegex(S.StateError, 'directory_path_invalid'): self.initialize()

    def test_symlink_hardlink_fifo_and_permissive_ledger_refused(self):
        self.initialize()
        ledger = self.target/'delivered.dat'
        ledger.chmod(0o644)
        with self.assertRaises(S.StateError), self.locked(): pass
        ledger.chmod(0o600)
        held = self.target/'held.dat'
        ledger.rename(held)
        ledger.symlink_to(held)
        with self.assertRaises(S.StateError), self.locked(): pass
        ledger.unlink()  # Synthetic test fixture only.
        os.link(held, ledger)
        with self.assertRaises(S.StateError), self.locked(): pass
        ledger.unlink()
        os.mkfifo(ledger, 0o600)
        with self.assertRaises(S.StateError), self.locked(): pass
        ledger.unlink()
        held.rename(ledger)
        with self.locked(): pass

    def test_seed_modification_and_invalid_ledger_refused(self):
        self.initialize()
        seed = self.target/'known-uids.dat'
        seed.write_bytes(b'u3\n')
        with self.assertRaisesRegex(S.StateError, 'seed_changed'), self.locked(): pass
        seed.write_bytes(b'u1\n')
        ledger = self.target/'delivered.dat'
        ledger.write_bytes(b'bad uid\n')
        with self.assertRaisesRegex(S.StateError, 'uid_state_invalid'), self.locked(): pass
        ledger.write_bytes(b'')
        with self.locked(): pass

    def test_malformed_and_duplicate_binding_keys_refused(self):
        self.initialize()
        ready = self.target/'ready.json'
        original = ready.read_bytes()
        for bad in (b'not-json', b'{"identity":1,"identity":2}', b'null'):
            ready.write_bytes(bad)
            with self.assertRaises(S.StateError), self.locked(): pass
        ready.write_bytes(original)
        with self.locked(): pass

    def test_state_size_and_invalid_uid_limits(self):
        with self.assertRaisesRegex(S.StateError, 'state_too_large'):
            S.read_private(self.seed, maximum=1)
        for data in (b'\n', b'bad uid\n', b'x'*4097, b'\x00'):
            with self.assertRaisesRegex(S.StateError, 'uid_state_invalid'): S.uids(data)

    def test_adapter_commit_and_reopen_preserve_duplicate_tracking(self):
        self.initialize()
        evidence = []
        for expected_count in (1, 0):
            with self.session() as runtime:
                evidence.append(runtime.args.evidence_dir)
                c = Cycle(FakePOP(2), runtime=runtime)
                try:
                    c.login()
                    self.assertTrue(c.command(b'STAT').startswith(
                        b'+OK %d ' % expected_count))
                    if expected_count: self.assertEqual(c.retrieve(1), BODY)
                    c.finish()
                    self.assertFalse(c.errors, c.errors)
                    self.assertNotIn('DELE', c.fake.commands)
                    self.assertEqual(runtime.state.delivered.read_bytes(), b'u2\n')
                    self.assertEqual(runtime.state.known.read_bytes(), b'u1\n')
                finally: c.cleanup()
        self.assertNotEqual(*evidence)
        self.assertTrue(all((p/'transaction-0001-upstream.txt').exists() for p in evidence))
        self.assertEqual(self.seed.read_bytes(), b'u1\n')

    def test_failure_latch_survives_session_reopening(self):
        self.initialize()
        with self.session() as runtime:
            c = Cycle(FakePOP(2, bad_identity=True), runtime=runtime)
            try:
                c.login()
                # This core closes the local stream on a body/UID mismatch;
                # it need not return a POP error line before disconnecting.
                self.assertFalse(c.command(b'RETR 1').startswith(b'+OK'))
                c.finish(quit=False)
                self.assertTrue(c.errors)
                self.assertEqual(runtime.state.delivered.read_bytes(), b'')
            finally: c.cleanup()
        self.assertTrue((self.target/'PROVIDER-ACCESS-DISABLED').exists())
        with self.assertRaisesRegex(S.StateError, 'provider_disabled'), self.session(): pass

    def test_transaction_limit_closed_runtime_and_overlap_refused(self):
        self.initialize()
        with self.session() as runtime:
            with patch.object(R.FULL, 'broker_transaction') as transaction:
                with runtime._transaction_lock:
                    with self.assertRaisesRegex(S.StateError, 'transaction_in_progress'):
                        runtime.transaction(None, None, factory=None)
                transaction.assert_not_called()
                for _ in range(10): runtime.transaction(None, None, factory=None)
                self.assertEqual(transaction.call_count, 10)
                with self.assertRaisesRegex(S.StateError, 'session_transaction_limit'):
                    runtime.transaction(None, None, factory=None)
                self.assertEqual(transaction.call_count, 10)
        with patch.object(R.FULL, 'broker_transaction') as transaction:
            with self.assertRaises(S.StateError): runtime.transaction(None, None, factory=None)
            transaction.assert_not_called()
        with self.locked(): pass


if __name__ == '__main__':
    unittest.main()
