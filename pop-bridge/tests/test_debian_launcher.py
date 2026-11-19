"""Focused launcher checks; synthetic paths, no processes or networking."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_full_mail import W
import account_state as S
import bridge_config as C
import debian_launcher as L


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='l-', dir=W/'records')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root/'r'
        self.run.mkdir(mode=0o700)
        self.account = C.Account('reader', 41, 'controlled.invalid', 995,
                                 'user', 21110, self.root/'seed')
        self.config = C.Config(self.root/'state', (self.account,))
        self.config.state_root.mkdir(mode=0o700)
        self.config.account_state(self.account).mkdir(mode=0o700)
        self.config_path = self.root/'config.json'
        S.new_file(self.config_path, b'{}')

    def command(self, role, **kwargs):
        return L.sandbox(role, self.config_path, self.config, self.account,
                         self.run, ['/usr/bin/true'], **kwargs)

    def test_only_broker_shares_network_and_gets_account_ledger(self):
        broker, client = self.command('broker'), self.command('client')
        self.assertIn('--share-net', broker)
        self.assertNotIn('--share-net', client)
        state = str(self.config.account_state(self.account))
        self.assertIn(state, broker)
        self.assertNotIn(state, client)
        for argv in (broker, client):
            self.assertIn('--unshare-all', argv)
            self.assertIn('--clearenv', argv)
            self.assertEqual(argv[argv.index('--cap-drop')+1], 'ALL')
            self.assertEqual(argv[-4:], ['--remount-ro', '/', '--', '/usr/bin/true'])
            self.assertNotIn('--die-with-parent', argv)
            self.assertNotIn('/root/.ssh', argv)

    def test_prefix_only_client_no_remote_display(self):
        prefix = self.root/'prefix'
        prefix.mkdir()
        self.assertIn(str(prefix), self.command('client', prefix=prefix))
        self.assertNotIn(str(prefix), self.command('broker', prefix=prefix))
        with self.assertRaisesRegex(S.StateError, 'local_X11_display_required'):
            self.command('client', display='remote:0')

    def test_symlink_and_reserved_bind_locations_rejected(self):
        alias = self.root/'alias'
        alias.symlink_to(self.run, target_is_directory=True)
        with self.assertRaises(S.StateError): L.path_checked(alias)
        with self.assertRaises(S.StateError): L.path_checked('/usr')

    def test_startup_failure_does_not_start_application(self):
        listener = Mock()
        listener.poll.return_value = 1
        with patch.object(L.subprocess, 'Popen', return_value=listener) as spawn:
            with self.assertRaisesRegex(S.StateError, 'listener_not_ready'):
                L.client_session(self.config_path, self.account, self.run, ['DO-NOT-RUN'])
            self.assertEqual(spawn.call_count, 1)
            self.assertNotIn('DO-NOT-RUN', spawn.call_args.args[0])

    def test_stale_readiness_refused_before_spawning(self):
        (self.run/'listener').mkdir()
        (self.run/'listener/ready').write_text('ready=yes\n')
        with patch.object(L.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(S.StateError, 'listener_directory_already_exists'):
                L.client_session(self.config_path, self.account, self.run, ['DO-NOT-RUN'])
            spawn.assert_not_called()

    def test_cli_parses_options_and_literal_application_without_shell(self):
        with patch.object(L, 'check_user'), patch.object(C, 'parse', return_value=self.config), patch.object(
                L, 'client_session') as session:
            result = L.main(['inside-client', '--config', str(self.config_path),
                             '--account', 'reader', '--run-directory', str(self.run),
                             '--', 'application', '--literal', 'a;b'])
        self.assertEqual(result, 0)
        self.assertEqual(session.call_args.args[-1], ['application', '--literal', 'a;b'])

    def test_cli_failure_is_nonzero(self):
        with patch.object(L, 'check_user', side_effect=S.StateError('private')), patch(
                'sys.stderr', new_callable=io.StringIO) as error:
            result = L.main(['broker', '--config', str(self.config_path),
                             '--account', 'reader', '--run-directory', str(self.run)])
        self.assertEqual(result, 1)
        self.assertIn('DEBIAN-LAUNCH-STOP', error.getvalue())
        self.assertNotIn('private', error.getvalue())

    def test_wine_application_wrapped_before_client_sandbox(self):
        with patch.object(L, 'check_user'), patch.object(C, 'parse', return_value=self.config), patch.object(
                L, 'sandbox', return_value=['/not-executed']) as builder, patch.object(L.os, 'execv'):
            L.main(['client', '--config', str(self.config_path), '--account', 'reader',
                    '--run-directory', str(self.run), '--prefix', '/home/example/prefix',
                    '--wine-root', '/home/example/wine', '--', 'C:\\Agent\\agent.exe'])
        application = builder.call_args.args[5]
        self.assertIn('/opt/agent-bridge/wine_lifetime.py', application)
        self.assertEqual(application[-1], 'C:\\Agent\\agent.exe')

    def multi_fixture(self):
        other_run = self.root/'r2'
        other_run.mkdir(mode=0o700)
        other = C.Account('other', 42, 'other.invalid', 995,
                          'user', 21111, self.root/'seed2')
        targets = [(self.account, self.run), (other, other_run)]
        listeners = [Mock(), Mock()]
        for listener in listeners:
            listener.poll.return_value = None
            listener.wait.return_value = 0
        app = Mock(returncode=0)
        app.poll.return_value = 0
        def spawn(command, **_):
            if command == ['APPLICATION']:
                for _, run in targets:
                    self.assertTrue((run/'listener/ready').is_file())
                return app
            name = command[command.index('--account')+1]
            index = [a.name for a, _ in targets].index(name)
            run = targets[index][1]
            (run/'listener').mkdir()
            (run/'listener/ready').write_text('ready=yes\n')
            return listeners[index]
        return targets, listeners, app, spawn

    def test_multi_ready_before_one_app_and_all_children_waited(self):
        targets, listeners, app, spawn = self.multi_fixture()
        with patch.object(L.subprocess, 'Popen', side_effect=spawn) as start, patch(
                'sys.stderr', new_callable=io.StringIO) as error:
            L.client_sessions(self.config_path, targets, ['APPLICATION'])
        self.assertEqual(start.call_count, 3)
        self.assertEqual(error.getvalue(), '')
        for listener in listeners:
            listener.terminate.assert_called()
            listener.wait.assert_called()
            listener.kill.assert_not_called()
        app.terminate.assert_not_called()

    def test_multi_early_failure_not_hidden_by_later_success(self):
        targets, listeners, app, spawn = self.multi_fixture()
        listeners[0].wait.return_value = 1
        with patch.object(L.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(S.StateError, 'client_session_not_accepted'):
                L.client_sessions(self.config_path, targets, ['APPLICATION'])
        for listener in listeners: listener.wait.assert_called()

    def test_multi_second_spawn_failure_cleans_first_without_app(self):
        targets, listeners, app, spawn = self.multi_fixture()
        with patch.object(L.subprocess, 'Popen', side_effect=[listeners[0], OSError('test')]) as start:
            with self.assertRaises(OSError):
                L.client_sessions(self.config_path, targets, ['APPLICATION'])
        self.assertEqual(start.call_count, 2)
        listeners[0].terminate.assert_called_once()
        listeners[0].wait.assert_called_once()
        listeners[0].kill.assert_not_called()

    def test_multi_one_dead_listener_prevents_application(self):
        targets, listeners, app, spawn = self.multi_fixture()
        listeners[0].poll.return_value = 1
        with patch.object(L.subprocess, 'Popen', side_effect=listeners) as start:
            with self.assertRaisesRegex(S.StateError, 'listener_not_ready'):
                L.client_sessions(self.config_path, targets, ['APPLICATION'])
        self.assertEqual(start.call_count, 2)
        listeners[1].terminate.assert_called_once()

    def test_multi_failure_while_app_open_warns_and_never_kills_app(self):
        targets, listeners, app, spawn = self.multi_fixture()
        app_started = False
        def start(command, **kwargs):
            nonlocal app_started
            result = spawn(command, **kwargs)
            if result is app: app_started = True
            return result
        listeners[0].poll.side_effect = lambda: 1 if app_started else None
        app.poll.side_effect = [None, 0, 0]
        with patch.object(L.subprocess, 'Popen', side_effect=start), patch.object(
                L.time, 'sleep'), patch('sys.stderr', new_callable=io.StringIO) as error:
            with self.assertRaisesRegex(S.StateError, 'client_session_not_accepted'):
                L.client_sessions(self.config_path, targets, ['APPLICATION'])
        self.assertEqual(error.getvalue().count('MAIL STOPPED'), 1)
        listeners[1].terminate.assert_called()
        app.terminate.assert_not_called()
        app.kill.assert_not_called()

    def test_multi_stale_second_run_prevents_any_spawn(self):
        targets, _, _, _ = self.multi_fixture()
        (targets[1][1]/'listener').mkdir()
        with patch.object(L.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(S.StateError, 'listener_directory_already_exists'):
                L.client_sessions(self.config_path, targets, ['APPLICATION'])
        spawn.assert_not_called()

    def test_multi_duplicate_run_or_account_rejected(self):
        targets, _, _, _ = self.multi_fixture()
        for invalid in ([targets[0], (targets[1][0], targets[0][1])],
                        [targets[0], (targets[0][0], targets[1][1])]):
            with patch.object(L.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(S.StateError, 'duplicate_listener_target'):
                    L.client_sessions(self.config_path, invalid, ['APPLICATION'])
                spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
