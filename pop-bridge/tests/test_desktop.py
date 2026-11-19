"""Synthetic lifecycle checks; never launches Wine, a browser or a provider."""
import json
import os
from unittest.mock import patch

import account_state as S
import desktop as D
import test_debian_launcher as T


class DesktopTests(T.unittest.TestCase):
    def setUp(self):
        T.LauncherTests.setUp(self)
        self.prefix = self.root/'prefix'
        self.prefix.mkdir(mode=0o700)
        exe = self.prefix/'drive_c/Program Files (x86)/Agent/agent.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'NOT AN EXECUTABLE')
        self.wine = self.root/'wine'
        (self.wine/'bin').mkdir(parents=True)
        for name in ('wine', 'wineserver'):
            p = self.wine/'bin'/name
            p.write_text('fixture only')
            p.chmod(0o700)
        self.settings = self.root/'desktop.json'
        self.data = dict(version=1, prefix=str(self.prefix), wine_root=str(self.wine),
                         config=str(self.config_path), services=None, session_root=str(self.run), browser=False)
        S.new_file(self.settings, json.dumps(self.data).encode())

    def load(self):
        with patch.object(D.C, 'parse', return_value=self.config):
            return D.load(self.settings)

    def record(self, completed=True):
        run = self.run/'run-fixture'
        run.mkdir(mode=0o700)
        raw = json.dumps(dict(run=str(run), settings_sha256='fixture')).encode()
        S.new_file(self.prefix/'.portable-agent.pending', raw)
        if completed: S.new_file(run/'completed.json', raw)
        return run, raw

    def test_settings_permissions_and_runtime(self):
        self.assertEqual(self.load()[0]['prefix'], self.prefix)
        self.prefix.chmod(0o770)
        with self.assertRaisesRegex(S.StateError, 'prefix_permissions'): self.load()
        self.prefix.chmod(0o700)
        (self.wine/'bin/wine').chmod(0o600)
        with self.assertRaisesRegex(S.StateError, 'runtime_missing'): self.load()

    def test_settings_digest_changes_with_private_mapping(self):
        first = self.load()[2]
        self.config_path.write_bytes(b'{"different":"synthetic"}')
        self.assertNotEqual(first, self.load()[2])

    def test_concurrent_prefix_lock_rejected(self):
        with D.lock(self.prefix):
            with self.assertRaisesRegex(S.StateError, 'already_running'):
                with D.lock(self.prefix): pass

    def test_lock_symlink_rejected(self):
        (self.prefix/'.portable-agent.lock').symlink_to(self.config_path)
        with self.assertRaises(OSError):
            with D.lock(self.prefix): pass

    def test_only_identical_success_can_clear_pending(self):
        self.record()
        with self.assertRaisesRegex(S.StateError, 'requires_review'):
            D.recover_completed(self.prefix, self.run, 'changed-settings')
        self.assertTrue((self.prefix/'.portable-agent.pending').exists())
        self.assertTrue(D.recover_completed(self.prefix, self.run, 'fixture'))
        self.assertFalse(D.recover_completed(self.prefix, self.run, 'fixture'))

    def test_missing_completion_retains_pending(self):
        self.record(completed=False)
        with self.assertRaises((OSError, S.StateError)):
            D.recover_completed(self.prefix, self.run, 'fixture')
        self.assertTrue((self.prefix/'.portable-agent.pending').exists())

    def test_mismatched_completion_retains_pending(self):
        run, _ = self.record()
        (run/'completed.json').write_bytes(b'{}')
        with self.assertRaises((S.StateError, OSError)): D.recover_completed(self.prefix, self.run, 'fixture')
        self.assertTrue((self.prefix/'.portable-agent.pending').exists())

    def test_malformed_pending_and_symlinks_refused(self):
        run, raw = self.record()
        pending = self.prefix/'.portable-agent.pending'
        pending.write_bytes(b'{broken')
        with self.assertRaises(S.StateError): D.recover_completed(self.prefix, self.run, 'fixture')
        pending.unlink()
        pending.symlink_to(run/'completed.json')
        with self.assertRaises((S.StateError, OSError)): D.recover_completed(self.prefix, self.run, 'fixture')

    def session(self, failure=False):
        data, config, digest = self.load()
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        def fake_launch(*args, **kwargs):
            pending = self.prefix/'.portable-agent.pending'
            self.assertTrue(pending.is_file())
            with self.assertRaisesRegex(S.StateError, 'already_running'):
                with D.lock(self.prefix): pass
            self.assertFalse(list(self.run.glob('run-*/completed.json')))
            if failure: raise S.StateError('synthetic_failure')
        with patch.object(D, 'check_user'), patch.object(D, 'load', return_value=(data, config, digest)), \
             patch.object(D.S, 'locked'), patch.object(D.L, 'sandbox'), \
             patch.object(D.L, 'launch_session', side_effect=fake_launch):
            return D.run(self.settings)

    def test_launch_failure_cannot_clear_pending(self):
        with self.assertRaisesRegex(S.StateError, 'synthetic_failure'): self.session(True)
        self.assertTrue((self.prefix/'.portable-agent.pending').exists())
        self.assertFalse(list(self.run.glob('run-*/completed.json')))

    def test_normal_finalization_only_then_clears_pending(self):
        self.assertEqual(self.session(), 0)
        self.assertFalse((self.prefix/'.portable-agent.pending').exists())
        self.assertEqual(len(list(self.run.glob('run-*/completed.json'))), 1)
