"""Focused lifetime tests; no Wine execution, profiles or provider connections."""
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_full_mail import W
import account_state as S
import wine_lifetime as L


class LifetimeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='wl-', dir=W/'records')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.prefix = self.root/'prefix'
        self.prefix.mkdir()
        self.runtime = self.root/'wine'
        (self.runtime/'bin').mkdir(parents=True)
        for name in ('wine', 'wineserver'):
            path = self.runtime/'bin'/name
            path.touch(mode=0o700)  # Non-executed fixture; process calls mocked below.
        self.events = []

    def execute(self, wine_status=0, server_status=0):
        process = Mock()
        process.wait.side_effect = lambda: self.events.append('wine-wait') or wine_status
        def wait_server(argv, **kwargs):
            self.assertEqual(argv, [str(self.runtime/'bin/wineserver'), '-w'])
            self.assertTrue(kwargs['start_new_session'])
            self.assertEqual(self.events[-1], 'wine-wait')
            self.events.append('server-wait')
            return Mock(returncode=server_status)
        with patch.object(L, 'check_user'), patch.dict(os.environ, WINEPREFIX=str(self.prefix)), patch.object(
                L.subprocess, 'Popen', return_value=process) as start, patch.object(
                L.subprocess, 'run', side_effect=wait_server):
            result = L.run(self.runtime, self.prefix, ['C:\\Agent\\agent.exe'])
            self.assertTrue(start.call_args.kwargs['start_new_session'])
            process.terminate.assert_not_called()
            process.kill.assert_not_called()
            return result

    def test_waits_for_server_after_initial_wine_return(self):
        old = signal.getsignal(signal.SIGTERM)
        self.assertEqual(self.execute(), 0)
        self.assertEqual(self.events[-1], 'server-wait')
        self.assertEqual(signal.getsignal(signal.SIGTERM), old)

    def test_early_wine_failure_is_not_hidden_by_successful_server_wait(self):
        with self.assertRaisesRegex(S.StateError, 'wine_or_server_exit_failed'):
            self.execute(wine_status=7)
        self.assertEqual(self.events[-1], 'server-wait')

    def test_server_failure_is_not_success(self):
        with self.assertRaisesRegex(S.StateError, 'wine_or_server_exit_failed'):
            self.execute(server_status=3)

    def test_wrong_prefix_rejected_before_launch(self):
        with patch.object(L, 'check_user'), patch.dict(os.environ, WINEPREFIX='/wrong'), patch.object(
                L.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(S.StateError, 'prefix_environment_mismatch'):
                L.run(self.runtime, self.prefix, ['agent.exe'])
            start.assert_not_called()

    def test_missing_server_rejected_before_launch(self):
        (self.runtime/'bin/wineserver').rename(self.runtime/'bin/held-server')
        with patch.object(L, 'check_user'), patch.dict(os.environ, WINEPREFIX=str(self.prefix)), patch.object(
                L.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(S.StateError, 'wine_runtime_invalid'):
                L.run(self.runtime, self.prefix, ['agent.exe'])
            start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
