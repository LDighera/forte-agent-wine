#!/usr/bin/env python3
"""Explicit synthetic socat integration check. Run only in an offline namespace.

No Agent, account configuration, TLS credentials or providers are used. The
caller supplies a NEW empty private directory; evidence is retained there.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import account_state as S
import relay_services as R

PAYLOAD = b'\x16\x03\x03\x00\x1bSynthetic opaque payload\r\n\x00\xff'


def receive_all(peer, n):
    value = b''
    while len(value) < n:
        chunk = peer.recv(n-len(value))
        if not chunk:
            raise RuntimeError('fixture_short_read')
        value += chunk
    return value


def inside(root, port):
    route = R.Route('fixture', 'smtp', '127.0.0.1', 9, port, 'implicit-tls')
    fleet = R.Fleet((route,), root, 'client')
    try:
        fleet.start()
        with socket.create_connection(('127.0.0.1', port), timeout=5) as peer:
            peer.sendall(PAYLOAD)
            assert receive_all(peer, len(PAYLOAD)) == PAYLOAD
            # Leave the forked connection open while stopping its namespace.
            fleet.close()
            assert peer.recv(1) == b''
        assert not fleet.failed()
    finally:
        fleet.close()
    assert not R.tcp_listening(port)
    print('PASS: separate-namespace relay round trip and listener teardown')


def outside(root):
    S.need({name for _, name in socket.if_nameindex()} == {'lo'}, 'offline_namespace_required')
    S.directory(root)
    S.need(not any(root.iterdir()), 'empty_probe_directory_required')
    source = Path(__file__).resolve().parents[1]
    errors, received = [], []
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        server.settimeout(15)
        route = R.Route('fixture', 'smtp', '127.0.0.1', server.getsockname()[1], 25465, 'implicit-tls')
        fleet = R.Fleet((route,), root, 'broker')
        def echo():
            try:
                for _ in range(2):
                    with server.accept()[0] as peer:
                        peer.settimeout(10)
                        data = receive_all(peer, len(PAYLOAD))
                        received.append(data)
                        peer.sendall(data)
                        assert peer.recv(1) == b''
            except BaseException as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=echo, daemon=True)
        thread.start()
        try:
            fleet.start()
            command = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--cap-drop', 'ALL',
                '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
                '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
                '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--clearenv',
                '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
                '--ro-bind', str(source), '/code', '--bind', str(root), '/work', '--',
                '/usr/bin/python3', '/code/tests/relay_namespace_probe.py', '--inside', '/work', '25465']
            result = subprocess.run(command, capture_output=True, timeout=30)
            (root/'client.stdout').write_bytes(result.stdout)
            (root/'client.stderr').write_bytes(result.stderr)
            S.need(result.returncode == 0, 'client_probe_failed')
            with socket.socket(socket.AF_UNIX) as peer:
                peer.settimeout(5)
                peer.connect(str(root/'fixture/relay.sock'))
                peer.sendall(PAYLOAD)
                S.need(receive_all(peer, len(PAYLOAD)) == PAYLOAD, 'broker_fixture_payload')
                fleet.close()
                S.need(peer.recv(1) == b'', 'broker_fork_still_open')
            thread.join(6)
            S.need(not thread.is_alive() and not errors and received == [PAYLOAD, PAYLOAD], 'provider_fixture_failed')
            S.need(not fleet.failed(), 'broker_unexpected_exit')
        finally:
            fleet.close()
        print('PASS: opaque bytes preserved, loopback-only provider, separate client network namespace')


if __name__ == '__main__':
    os.umask(0o077)
    if len(sys.argv) == 4 and sys.argv[1] == '--inside':
        inside(Path(sys.argv[2]), int(sys.argv[3]))
    elif len(sys.argv) == 2:
        outside(Path(sys.argv[1]))
    else:
        raise SystemExit('Usage: relay_namespace_probe.py NEW-EMPTY-PRIVATE-DIRECTORY')
