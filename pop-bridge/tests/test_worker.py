"""Synthetic worker lifecycle tests; only local Unix sockets, no services."""
import io
import json
from pathlib import Path
import signal
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from test_full_mail import W, FakePOP, BODY
import account_state as S
import bridge_config as C
import bridge_worker as K


class WorkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='w-', dir=W/'records')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root/'state').mkdir(mode=0o700)
        seed = self.root/'seed'
        S.new_file(seed, b'u1\n')
        self.account = C.Account('reader', 41, 'controlled.invalid', 995, 'user', 21110, seed)
        self.config = C.Config(self.root/'state', (self.account,))
        S.initialize(self.config, self.account)
        self.run = self.root/'r'
        self.run.mkdir(mode=0o700)
        self.stop = threading.Event()
        self.results, self.errors, self.threads = [], [], []
        self.addCleanup(self.finish)

    def start(self, function, **kwargs):
        def work():
            try: self.results.append(function(self.config, self.account, self.run, self.stop, **kwargs))
            except Exception as exc: self.errors.append(exc)
        thread = threading.Thread(target=work, daemon=True)
        self.threads.append(thread)
        thread.start()

    def wait_for(self, path):
        until = time.monotonic()+3
        while not path.exists() and not self.errors and time.monotonic() < until:
            time.sleep(0.01)
        self.assertTrue(path.exists(), self.errors)

    def finish(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(4)
            self.assertFalse(thread.is_alive(), 'worker did not stop')

    def test_startup_delivery_and_orderly_stop(self):
        fake = FakePOP(2)
        self.start(K.broker, factory=fake.factory, limit=1)
        self.wait_for(self.run/'broker-ready.json')
        client_path = self.run/'p.sock'
        # Test transport substitutes Unix socket for listener TCP only. Production
        # bind arguments are asserted; actual network namespace isolation is NOT
        # claimed as integration-tested by this fixture.
        class LocalServer:
            def __init__(inner, family, kind):
                self.assertEqual((family, kind), (socket.AF_INET, socket.SOCK_STREAM))
                inner.sock = socket.socket(socket.AF_UNIX)
            def bind(inner, address):
                self.assertEqual(address, ('127.0.0.1', 21110))
                inner.sock.bind(str(client_path))
            def __getattr__(inner, name): return getattr(inner.sock, name)
            def __enter__(inner): return inner
            def __exit__(inner, *_): inner.sock.close()
        with patch.object(K, 'isolated_listener'):
            self.start(K.listener, limit=1, server_factory=LocalServer)
            self.wait_for(self.run/'listener'/'ready')
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(3)
                client.connect(str(client_path))
                with client.makefile('rwb', buffering=0) as stream:
                    self.assertTrue(stream.readline().startswith(b'+OK'))
                    for command in (b'USER user', b'PASS synthetic', b'RETR 1'):
                        stream.write(command+b'\r\n')
                        self.assertTrue(stream.readline().startswith(b'+OK'))
                    body = []
                    while True:
                        line = stream.readline()
                        self.assertTrue(line)
                        if line == b'.\r\n': break
                        body.append(line[1:] if line.startswith(b'..') else line)
                    self.assertEqual(b''.join(body), BODY)
                    stream.write(b'DELE 1\r\n')
                    self.assertTrue(stream.readline().startswith(b'-ERR'))
                    stream.write(b'QUIT\r\n')
                    self.assertTrue(stream.readline().startswith(b'+OK'))
            self.finish()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(sorted(self.results), [0, 0])
        with S.locked(self.config, self.account) as state:
            self.assertEqual(state.delivered.read_bytes(), b'u2\n')
        self.assertNotIn('DELE', fake.commands)
        for path in ('b.sock', 'broker-ready.json', 'listener/ready'):
            self.assertFalse((self.run/path).exists())
        for role in ('broker', 'listener'):
            self.assertIn('status=stopped', (self.run/role/'aggregate.txt').read_text())

    def test_idle_stop_releases_lock_without_provider_contact(self):
        def forbidden(*_, **__): self.fail('unexpected provider contact')
        self.start(K.broker, factory=forbidden)
        self.wait_for(self.run/'broker-ready.json')
        with self.assertRaises(S.StateError), S.locked(self.config, self.account): pass
        self.finish()
        self.assertFalse(self.errors, self.errors)
        with S.locked(self.config, self.account): pass

    def test_existing_socket_or_file_is_not_removed(self):
        sentinel = self.run/'b.sock'
        sentinel.write_bytes(b'keep')
        with self.assertRaises(S.StateError):
            K.broker(self.config, self.account, self.run, self.stop)
        self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_protocol_failure_is_not_masked_by_shutdown(self):
        def forbidden(*_, **__): self.fail('unexpected provider contact')
        self.start(K.broker, factory=forbidden)
        self.wait_for(self.run/'broker-ready.json')
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(3)
            client.connect(str(self.run/'b.sock'))
            client.sendall(b'BAD!!')
            self.assertTrue(client.recv(256))
        self.finish()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.results, [])
        self.assertIn('status=failed', (self.run/'broker'/'aggregate.txt').read_text())
        self.assertFalse((self.run/'broker-ready.json').exists())
        self.assertFalse((self.run/'b.sock').exists())
        self.assertTrue((self.run/'failure.json').is_file())
        self.assertIn('Provider login was not completed', K.D.describe(self.run))
        with self.assertRaisesRegex(S.StateError, 'provider_disabled'), S.locked(
                self.config, self.account): pass

    def test_listener_rejects_wrong_account_before_bind(self):
        S.new_file(self.run/'broker-ready.json', json.dumps(
            dict(identity={}, namespace='synthetic')).encode())
        with patch.object(K.socket, 'socket') as factory:
            with self.assertRaisesRegex(S.StateError, 'broker_identity_mismatch'):
                K.listener(self.config, self.account, self.run, self.stop)
            factory.assert_not_called()

    def test_notice_write_failure_does_not_mask_transaction_failure(self):
        def forbidden(*_, **__): self.fail('unexpected provider contact')
        with patch.object(K.D, 'record', side_effect=OSError('PRIVATE')):
            self.start(K.broker, factory=forbidden)
            self.wait_for(self.run/'broker-ready.json')
            with socket.socket(socket.AF_UNIX) as client:
                client.settimeout(3)
                client.connect(str(self.run/'b.sock'))
                client.sendall(b'BAD!!')
                self.assertTrue(client.recv(256))
            self.finish()
        self.assertEqual(len(self.errors), 1)
        self.assertIn('full_mail_protocol_required', str(self.errors[0]))
        self.assertNotIn('PRIVATE', str(self.errors[0]))
        self.assertFalse((self.run/'failure.json').exists())
        self.assertIn('status=failed', (self.run/'broker'/'aggregate.txt').read_text())
        with self.assertRaisesRegex(S.StateError, 'provider_disabled'), S.locked(
                self.config, self.account): pass

    def test_network_guard_rejects_shared_namespace_and_external_interfaces(self):
        for current, interfaces, allowed in (
                ('broker', [(1, 'lo')], False),
                ('isolated', [(1, 'lo'), (2, 'eth0')], False),
                ('isolated', [(1, 'lo')], True)):
            with patch.object(K, 'network_namespace', return_value=current), patch.object(
                    K.socket, 'if_nameindex', return_value=interfaces):
                if allowed: K.isolated_listener('broker')
                else:
                    with self.assertRaises(S.StateError): K.isolated_listener('broker')

    def test_cleanup_preserves_replaced_path(self):
        path = self.run/'owned'
        path.write_bytes(b'old')
        identity = K.owned_identity(path)
        path.rename(self.run/'old-retained')
        path.write_bytes(b'new')
        K.remove_owned(path, identity)
        self.assertEqual(path.read_bytes(), b'new')

    def test_signal_requests_stop_and_restores_handlers(self):
        old = signal.getsignal(signal.SIGTERM)
        with K.stop_signals(self.stop):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
            self.assertTrue(self.stop.is_set())
        self.assertEqual(signal.getsignal(signal.SIGTERM), old)

    def test_cli_root_rejection_is_nonzero_without_raw_exception(self):
        with patch.object(K, 'check_user', side_effect=S.StateError('PRIVATE')), patch(
                'sys.stderr', new_callable=io.StringIO) as error:
            code = K.main(['broker', '--config', str(self.root/'missing'),
                           '--account', 'reader', '--run-directory', str(self.run)])
        self.assertEqual(code, 1)
        self.assertIn('BRIDGE-WORKER-STOP', error.getvalue())
        self.assertNotIn('PRIVATE', error.getvalue())
        self.assertEqual(list(self.run.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
