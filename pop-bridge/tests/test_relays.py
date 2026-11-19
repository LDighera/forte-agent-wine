import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import account_state as S
import relay_services as R
import test_debian_launcher as T
import debian_launcher as L


class RelayTests(unittest.TestCase):
    setUp = T.LauncherTests.setUp

    def row(self, **changes):
        return dict(dict(name='outgoing', kind='smtp', host='smtp.example.invalid',
                         port=465, listen_port=2465, security='implicit-tls'), **changes)

    def parse(self, rows=None, ports=()):
        return R.parse(json.dumps(dict(version=1, routes=rows or [self.row()])).encode(), ports)

    def test_transport_and_hostname_preserved(self):
        route, = self.parse()
        self.assertEqual(route.children, 1)
        hostfile = R.hosts([route])
        self.assertIn(b'127.0.0.1 smtp.example.invalid\n', hostfile)
        d = self.run/'transport'
        d.mkdir(mode=0o700)
        broker = R.broker_command(route, d)
        self.assertEqual(broker[-1], 'TCP4:smtp.example.invalid:465,connect-timeout=20')
        self.assertIn('--share-net', broker)
        self.assertNotIn(str(self.config.state_root), broker)
        self.assertNotIn(str(self.config_path), broker)
        self.assertNotIn('-v', broker)
        self.assertNotIn('-x', broker)
        self.assertEqual(R.client_command(route, d)[-1], 'UNIX-CONNECT:/transport/relay.sock')

    def test_rejects_injection_ports_and_plain_smtp(self):
        for change in [dict(host='smtp.invalid,exec=/bin/sh'), dict(host='-host'),
                       dict(name='../escape'), dict(port=True), dict(listen_port=465),
                       dict(listen_port=65536), dict(security='plain'), dict(kind='pop')]:
            with self.subTest(change=change), self.assertRaises(S.StateError):
                self.parse([self.row(**change)])

    def test_conflicts_with_pop_and_other_routes(self):
        with self.assertRaisesRegex(S.StateError, 'route_conflict'):
            self.parse(ports=[2465])
        with self.assertRaisesRegex(S.StateError, 'route_conflict'):
            self.parse([self.row(), self.row(name='second')])
        with self.assertRaisesRegex(S.StateError, 'route_conflict'):
            self.parse([self.row(), self.row(listen_port=2466)])

    def test_nntp_plain_requires_explicit_selection(self):
        route, = self.parse([self.row(kind='nntp', security='plain', port=119)])
        self.assertEqual(route.children, 4)
        with self.assertRaises(S.StateError):
            self.parse([dict(name='news', kind='nntp')])

    def test_duplicate_fields_and_oversized_config_rejected(self):
        for raw in [b'{"version":1,"version":1,"routes":[]}', b'x'*16385]:
            with self.assertRaises(S.StateError): R.parse(raw)

    def test_zero_routes_no_processes(self):
        fleet = R.Fleet((), None, 'broker')
        with patch.object(R.subprocess, 'Popen') as spawn:
            fleet.start()
            self.assertFalse(fleet.failed())
            fleet.close()
        spawn.assert_not_called()

    def test_partial_start_failure_collects_started_relays(self):
        routes = self.parse([self.row(), self.row(name='second', listen_port=2466)])
        fleet = R.Fleet(routes, self.run, 'broker')
        process = Mock()
        process.poll.return_value = None
        with patch.object(R.subprocess, 'Popen', side_effect=[process, OSError('test')]):
            with self.assertRaises(OSError): fleet.start()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)

    def test_dead_relay_stops_startup(self):
        fleet = R.Fleet(self.parse(), self.run, 'broker')
        process = Mock()
        process.poll.return_value = 1
        with patch.object(R.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(S.StateError, 'relay_start_failed'): fleet.start()
        process.wait.assert_called_once()

    def test_cleanup_checks_every_process_and_reports_timeout(self):
        fleet = R.Fleet((), None, 'broker')
        first, second = Mock(), Mock()
        first.poll.return_value = second.poll.return_value = None
        first.wait.side_effect = subprocess.TimeoutExpired('fixture', 10)
        fleet.processes = [first, second]
        with self.assertRaisesRegex(S.StateError, 'relay_cleanup_timeout'): fleet.close()
        second.wait.assert_called_once()
        self.assertEqual(fleet.processes, [first])
        self.assertTrue(fleet.failed())
        first.wait.side_effect = None
        with self.assertRaisesRegex(S.StateError, 'relay_cleanup_timeout'): fleet.close()
        first.terminate.assert_called_once()
        self.assertTrue(fleet.failed())

    def test_client_rejects_host_network_and_occupied_port(self):
        fleet = R.Fleet(self.parse(), self.run, 'client')
        with patch.object(R.socket, 'if_nameindex', return_value=[(1, 'lo'), (2, 'eth0')]), patch.object(
                R.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(S.StateError, 'relay_client_not_isolated'): fleet.start()
            spawn.assert_not_called()
        with patch.object(R.socket, 'if_nameindex', return_value=[(1, 'lo')]), patch.object(
                R, 'tcp_listening', return_value=True), patch.object(R.subprocess, 'Popen') as spawn:
            with self.assertRaisesRegex(S.StateError, 'relay_port_in_use'): fleet.start()
            spawn.assert_not_called()

    def test_client_mounts_services_and_private_host_mapping_only(self):
        service = self.root/'services.json'
        S.new_file(service, json.dumps(dict(version=1, routes=[self.row()])).encode())
        S.new_file(self.run/'.services-hosts', R.hosts(self.parse()))
        command = L.sandbox('client', self.config_path, self.config, self.account,
                            self.run, ['APP'], services_path=service)
        self.assertNotIn('--share-net', command)
        self.assertIn(str(self.run/'.services-hosts'), command)
        self.assertNotIn(str(self.config.state_root), command)


if __name__ == '__main__': unittest.main()
