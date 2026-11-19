"""Outer coordinator tests: real command construction, mocked child processes."""
import io
import json
import signal
import unittest
from unittest.mock import Mock, patch

import test_debian_launcher as T
import account_state as S
import bridge_config as C
import bridge_worker as K
import debian_launcher as L


class SessionTests(unittest.TestCase):
    setUp = T.LauncherTests.setUp

    def fixture(self):
        other = C.Account('other', 42, 'controlled.invalid', 995, 'user2', 21111,
                          self.root/'seed2')
        self.config = C.Config(self.config.state_root, (self.account, other))
        self.config.account_state(other).mkdir(mode=0o700)
        brokers = [Mock(), Mock()]
        for broker in brokers:
            broker.poll.return_value = None
            broker.wait.return_value = 0
        client = Mock(returncode=0)
        client.poll.return_value = 0
        def spawn(command, **kwargs):
            if 'inside-client-all' in command:
                self.assertNotIn('--share-net', command)
                for account in self.config.accounts:
                    self.assertTrue((self.run/account.name/'broker-ready.json').is_file())
                    self.assertNotIn(str(self.config.account_state(account)), command)
                return client
            self.assertIn('--share-net', command)
            name = command[command.index('--account')+1]
            index = [a.name for a in self.config.accounts].index(name)
            for account in self.config.accounts:
                self.assertEqual(str(self.config.account_state(account)) in command,
                                 account.name == name)
            S.new_file(self.run/name/'broker-ready.json', b'{}')
            return brokers[index]
        return brokers, client, spawn

    def run_session(self):
        L.launch_session(self.config_path, self.config, self.run, ['APPLICATION'])

    def test_success_is_quiet_and_stops_brokers_without_signalling_sandboxes(self):
        brokers, client, spawn = self.fixture()
        with patch.object(L.subprocess, 'Popen', side_effect=spawn) as start, patch(
                'sys.stderr', new_callable=io.StringIO) as error:
            self.run_session()
        self.assertEqual(start.call_count, 3)
        self.assertEqual(error.getvalue(), '')
        for account in self.config.accounts:
            self.assertTrue((self.run/account.name/'stop').is_file())
        for process in brokers+[client]:
            process.terminate.assert_not_called()
            process.kill.assert_not_called()
        for broker in brokers: broker.wait.assert_called()

    def test_early_failed_exit_not_hidden_by_later_success(self):
        brokers, client, spawn = self.fixture()
        brokers[0].wait.return_value = 1
        with patch.object(L.subprocess, 'Popen', side_effect=spawn):
            with self.assertRaisesRegex(S.StateError, 'multi_session_not_accepted'):
                self.run_session()
        brokers[1].wait.assert_called()

    def test_partial_spawn_failure_marks_and_waits_without_client(self):
        brokers, client, spawn = self.fixture()
        with patch.object(L.subprocess, 'Popen', side_effect=[brokers[0], OSError('test')]) as start:
            with self.assertRaises(OSError): self.run_session()
        self.assertEqual(start.call_count, 2)
        brokers[0].wait.assert_called_once()
        self.assertTrue((self.run/'reader/stop').is_file())

    def test_dead_broker_prevents_client_start(self):
        brokers, client, spawn = self.fixture()
        brokers[0].poll.return_value = 1
        with patch.object(L.subprocess, 'Popen', side_effect=spawn) as start:
            with self.assertRaisesRegex(S.StateError, 'broker_stopped_before_client'):
                self.run_session()
        self.assertEqual(start.call_count, 2)

    def test_stop_request_waits_for_client_and_rejects(self):
        brokers, client, spawn = self.fixture()
        polls = 0
        def poll():
            nonlocal polls
            polls += 1
            if polls == 1:
                signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                return None
            return 0
        client.poll.side_effect = poll
        with patch.object(L.subprocess, 'Popen', side_effect=spawn), patch.object(
                L.time, 'sleep'), patch('sys.stderr', new_callable=io.StringIO) as error:
            with self.assertRaisesRegex(S.StateError, 'multi_session_not_accepted'):
                self.run_session()
        self.assertEqual(error.getvalue().count('SESSION STOP'), 1)
        client.terminate.assert_not_called()

    def test_invalid_client_mount_prevents_all_spawns(self):
        self.fixture()
        with patch.object(L.subprocess, 'Popen') as start:
            with self.assertRaises(S.StateError):
                L.launch_session(self.config_path, self.config, self.run, ['APP'], display='remote:0')
        start.assert_not_called()

    def test_failed_broker_notice_reports_inventory_failure_without_success(self):
        brokers, client, spawn = self.fixture()
        original_spawn = spawn
        def start(command, **kwargs):
            result = original_spawn(command, **kwargs)
            if 'inside-client-all' in command:
                S.new_file(self.run/self.account.name/'failure.json', json.dumps(
                    dict(phase='list', detail='connection_closed', login='yes')).encode())
                brokers[0].poll.return_value = 1
                brokers[0].wait.return_value = 1
            return result
        client.poll.side_effect = [None, 0, 0]
        notice = Mock()
        with patch.object(L.subprocess, 'Popen', side_effect=start), patch.object(
                L.time, 'sleep'), patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaisesRegex(S.StateError, 'multi_session_not_accepted'):
                L.launch_session(self.config_path, self.config, self.run, ['APP'], notify=notice)
        notice.assert_called_once()
        text = notice.call_args.args[0]
        self.assertIn('Provider login succeeded', text)
        self.assertIn('listing message sizes failed', text)
        self.assertIn('Stop sending and polling', text)
        client.kill.assert_not_called()
        client.terminate.assert_not_called()

    def test_nonempty_session_not_reused(self):
        S.new_file(self.run/'existing', b'keep')
        with patch.object(L.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(S.StateError, 'session_directory_not_empty'):
                self.run_session()
        start.assert_not_called()

    def test_worker_stop_marker_and_signal_event(self):
        stop = K.RunStop(self.run)
        self.assertFalse(stop.is_set())
        S.new_file(self.run/'stop', b'stop')
        self.assertTrue(stop.is_set())
        other = K.RunStop(self.root/'absent')
        other.set()
        self.assertTrue(other.is_set())

    def test_session_cli_dispatches_all_accounts_without_shell(self):
        with patch.object(L, 'check_user'), patch.object(C, 'parse', return_value=self.config), patch.object(
                L, 'launch_session') as launch:
            result = L.main(['session', '--config', str(self.config_path),
                             '--run-directory', str(self.run), '--', 'app', 'a;b'])
        self.assertEqual(result, 0)
        self.assertEqual(launch.call_args.args[-1], ['app', 'a;b'])

    def test_all_accounts_cli_refuses_silent_account_selection(self):
        with patch.object(L, 'check_user'), patch.object(C, 'parse', return_value=self.config), patch.object(
                L, 'launch_session') as launch, patch('sys.stderr', new_callable=io.StringIO):
            result = L.main(['session', '--config', str(self.config_path), '--account', 'reader',
                             '--run-directory', str(self.run), '--', 'app'])
        self.assertEqual(result, 1)
        launch.assert_not_called()


if __name__ == '__main__': unittest.main()
