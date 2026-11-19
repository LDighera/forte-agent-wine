"""Fixed-endpoint SMTP/NNTP transports using socat, never a protocol reimplementation.

No connection on import. Provider-side transports see only their own socket
directory, not Agent's prefix, POP ledgers, credentials or desktop session.
TLS remains Agent-to-provider; this module does not authenticate or log payloads.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import time

import account_state as S
import bridge_config as C


@dataclass(frozen=True)
class Route:
    name: str
    kind: str
    host: str
    port: int
    listen_port: int
    security: str

    @property
    def children(self):
        return 1 if self.kind == 'smtp' else 4


def parse(raw, pop_ports=()):
    S.need(isinstance(raw, bytes) and len(raw) <= 16384, 'services_size_invalid')
    try:
        data = json.loads(raw, object_pairs_hook=C.unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise S.StateError('services_json_invalid') from None
    S.need(type(data) is dict and set(data) == {'version', 'routes'} and
           type(data['version']) is int and data['version'] == 1 and
           type(data['routes']) is list and len(data['routes']) <= 16,
           'services_fields_invalid')
    routes = []
    for row in data['routes']:
        S.need(type(row) is dict and set(row) ==
               {'name', 'kind', 'host', 'port', 'listen_port', 'security'},
               'route_fields_invalid')
        S.need(type(row['name']) is str and
               re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', row['name']), 'route_name_invalid')
        S.need(row['kind'] in ('smtp', 'nntp'), 'route_kind_invalid')
        host = row['host']
        S.need(type(host) is str and 1 <= len(host) <= 253 and all(
            re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
            for label in host.split('.')), 'route_host_invalid')
        for key, lower in [('port', 1), ('listen_port', 1024)]:
            S.need(type(row[key]) is int and lower <= row[key] <= 65535,
                   'route_port_invalid')
        S.need(row['security'] in ('implicit-tls', 'starttls', 'plain') and
               (row['kind'] != 'smtp' or row['security'] != 'plain'),
               'route_security_invalid')
        routes.append(Route(**dict(row, host=host.lower())))
    S.need(len({r.name for r in routes}) == len(routes) and
           len({r.listen_port for r in routes}) == len(routes) and
           not set(pop_ports).intersection(r.listen_port for r in routes),
           'route_conflict')
    return tuple(routes)


def load(path, config):
    S.need(path.resolve() == path, 'services_path_alias')
    return parse(S.read_private(path, 16384), [a.listen_port for a in config.accounts])


def hosts(routes):
    # The application uses the real provider hostname so certificate verification
    # and SNI retain the original name, but resolves it only to local listeners.
    return ('127.0.0.1 localhost\n::1 localhost\n' + ''.join(
        '127.0.0.1 ' + host + '\n' for host in sorted({r.host for r in routes}))).encode()


def broker_command(route, directory):
    S.directory(directory)
    args = ['/usr/bin/bwrap', '--unshare-all', '--share-net', '--die-with-parent',
            '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr',
            '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
            '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
            '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp', '--chdir', '/tmp']
    for name in ('passwd', 'group', 'nsswitch.conf', 'hosts', 'resolv.conf'):
        if Path('/etc', name).exists():
            args += ['--ro-bind', '/etc/'+name, '/etc/'+name]
    return args + ['--bind', str(directory), '/transport', '--remount-ro', '/', '--',
        '/usr/bin/socat', '-d', '-d',
        f'UNIX-LISTEN:/transport/relay.sock,fork,max-children={route.children},mode=0600',
        f'TCP4:{route.host}:{route.port},connect-timeout=20']


def client_command(route, directory):
    # Normalize the potentially punctuated user path through a private bind.
    # Keep this client's network namespace, isolate process lifetime/filesystem.
    return ['/usr/bin/bwrap', '--unshare-all', '--share-net', '--die-with-parent',
        '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr',
        '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
        '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'HOME', '/tmp', '--chdir', '/tmp',
        '--bind', str(directory), '/transport', '--remount-ro', '/', '--',
        '/usr/bin/socat', '-d', '-d',
        f'TCP4-LISTEN:{route.listen_port},bind=127.0.0.1,reuseaddr,fork,max-children={route.children}',
        'UNIX-CONNECT:/transport/relay.sock']


def tcp_listening(port):
    # Do not connect to test readiness: that would contact a real provider.
    with open('/proc/net/tcp', encoding='ascii') as stream:
        for line in stream:
            fields = line.split()
            if len(fields) >= 4 and fields[1] == f'0100007F:{port:04X}' and fields[3] == '0A':
                return True
    return False


class Fleet:
    """Each process is a disposable relay-only PID namespace; never contains Wine."""
    def __init__(self, routes, root, role):
        S.need(role in ('broker', 'client'), 'relay_role_invalid')
        self.routes, self.root, self.role = routes, root, role
        self.processes, self.logs = [], []
        self.cleanup_failed = False
        self.stop_requested = set()

    def start(self):
        if not self.routes:
            return
        S.directory(self.root)
        if self.role == 'client':
            S.need({name for _, name in socket.if_nameindex()} == {'lo'},
                   'relay_client_not_isolated')
            S.need(not any(tcp_listening(r.listen_port) for r in self.routes),
                   'relay_port_in_use')
        commands = []
        for route in self.routes:
            directory = self.root/route.name
            if self.role == 'broker':
                directory.mkdir(mode=0o700)
            S.directory(directory)
            if self.role == 'client':
                info = (directory/'relay.sock').lstat()
                S.need(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid() and
                       stat.S_IMODE(info.st_mode) == 0o600, 'relay_socket_invalid')
            commands.append((broker_command if self.role == 'broker' else client_command)(route, directory))
        try:
            for route, command in zip(self.routes, commands):
                log = (self.root/route.name/(self.role+'.log')).open('xb')
                self.logs.append(log)
                self.processes.append(subprocess.Popen(command, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True))
            deadline = time.monotonic()+15
            while True:
                S.need(not self.failed(), 'relay_start_failed')
                ready = all(tcp_listening(r.listen_port) for r in self.routes) if self.role == 'client' else all(
                    (self.root/r.name/'relay.sock').is_socket() for r in self.routes)
                if ready:
                    return
                S.need(time.monotonic() < deadline, 'relay_ready_timeout')
                time.sleep(0.05)
        except BaseException:
            self.close()
            raise

    def failed(self):
        return self.cleanup_failed or any(p.poll() is not None for p in self.processes)

    def close(self):
        # SIGTERM tears down only the private relay PID namespace and children.
        # Always collect every child even if one failed; no retry or provider I/O.
        pending = []
        for process in self.processes:
            if process.poll() is None and process not in self.stop_requested:
                process.terminate()
                self.stop_requested.add(process)
        for process in self.processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pending.append(process)
                self.cleanup_failed = True
        for log in self.logs:
            log.close()
        self.processes, self.logs = pending, []
        # A later close may collect the process, but cannot erase the failure.
        S.need(not self.cleanup_failed, 'relay_cleanup_timeout')
