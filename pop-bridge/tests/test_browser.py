"""Synthetic private D-Bus service; never connects to the desktop or a browser."""
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch

import account_state as S
import browser_portal as B
import open_url as U
import test_debian_launcher as T


class BrowserTests(unittest.TestCase):
    setUp = T.LauncherTests.setUp

    def test_supported_links_are_literal(self):
        self.assertEqual(U.validate('https://example.invalid/a?x=1&y=2'), 'https://example.invalid/a?x=1&y=2')
        for value in ('file:///etc/passwd', 'javascript:alert(1)', '--help', 'https://user:secret@host',
                      'https://host/\nprivate', 'https://host\\escape', 'https://host:invalid', 'https://', 'x'*8193):
            with self.subTest(value=value[:20]), self.assertRaises(ValueError): U.validate(value)

    def test_bus_rejects_nonlocal_sources(self):
        for value in ('tcp:host=localhost', 'unix:abstract=bus', 'unix:path=/tmp/missing;unix:path=/tmp/other'):
            with self.assertRaises((S.StateError, OSError)): B.command(value, self.run/'socket')

    def test_timeout_is_sticky_and_handle_retained(self):
        with patch.object(B, 'command', return_value=['fixture']):
            portal = B.Portal('fixture', self.run)
        p = portal.process = Mock()
        p.poll.return_value = None
        p.wait.side_effect = subprocess.TimeoutExpired('fixture', 5)
        with self.assertRaises(S.StateError): portal.close()
        self.assertIs(portal.process, p)
        p.wait.side_effect = None
        with self.assertRaises(S.StateError): portal.close()
        p.terminate.assert_called_once()

    def test_actual_proxy_allows_openuri_and_denies_unrelated_methods(self):
        from gi.repository import Gio, GLib
        daemon = subprocess.Popen(['/usr/bin/dbus-daemon', '--session', '--nofork', '--print-address=1'],
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(daemon.stdout.close)
        self.addCleanup(lambda: (daemon.terminate(), daemon.wait(timeout=5)))
        self.assertTrue(select.select([daemon.stdout], [], [], 5)[0])
        address = daemon.stdout.readline().strip()
        service = subprocess.Popen([sys.executable, '-B', str(Path(__file__).with_name('support')/'fake_portal.py'), address],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(service.stdout.close)
        self.addCleanup(lambda: (service.terminate(), service.wait(timeout=5)))
        self.assertTrue(select.select([service.stdout], [], [], 5)[0])
        self.assertEqual(service.stdout.readline().strip(), 'READY')
        portal = B.Portal(address, self.run)
        self.addCleanup(portal.close)
        portal.start()
        proxy_address = 'unix:path='+str(portal.destination)
        with patch.dict(os.environ, DBUS_SESSION_BUS_ADDRESS=proxy_address):
            U.open_url('https://example.invalid/synthetic?quote=%22&literal=$nothing', 2000)
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                U.open_url('https://example.invalid/cancel', 2000)
        conn = Gio.DBusConnection.new_for_address_sync(proxy_address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        try:
            for interface, method in [('org.freedesktop.portal.OpenURI', 'Forbidden'),
                                       ('org.freedesktop.portal.Secret', 'RetrieveSecret')]:
                with self.assertRaises(GLib.Error):
                    conn.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
                        interface, method, None, None, Gio.DBusCallFlags.NONE, 2000, None)
        finally:
            conn.close_sync(None)
        self.assertEqual(service.stdout.readline().strip(), 'OPENURI')
        self.assertEqual(service.stdout.readline().strip(), 'OPENURI')
        child_root = self.run/'owner'
        child_root.mkdir(mode=0o700)
        code = ('from pathlib import Path; import sys, signal; import browser_portal as B; '
                'p=B.Portal(sys.argv[1],Path(sys.argv[2])); p.start(); '
                'print(p.process.pid,flush=True); signal.pause()')
        owner = subprocess.Popen([sys.executable, '-B', '-c', code, address, str(child_root)],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            self.assertTrue(select.select([owner.stdout], [], [], 5)[0])
            proxy_pid = int(owner.stdout.readline())
        finally:
            owner.terminate()
            owner.wait(timeout=5)
            owner.stdout.close()
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            status = Path('/proc')/str(proxy_pid)/'stat'
            if not status.exists() or status.read_text().split(') ', 1)[1].startswith('Z '): break
            time.sleep(0.02)
        else:
            self.fail('proxy survived abrupt launcher exit')
