#!/usr/bin/env python3
"""Private portable worker candidate. NOT an installer or an Agent launcher.

Two separate processes are required: broker with provider access; listener in
Agent's loopback-only network namespace. Do not activate before release review.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import threading
from types import SimpleNamespace

import account_state as S
import bridge_config as C
import runtime_adapter as R
from setup_bridge import check_user


def network_namespace():
    return os.readlink('/proc/self/ns/net')


def isolated_listener(broker_namespace):
    S.need(network_namespace() != broker_namespace and
           {name for _, name in socket.if_nameindex()} == {'lo'},
           'listener_requires_separate_loopback_namespace')


def owned_identity(path):
    info = path.lstat()
    return info.st_dev, info.st_ino


def remove_owned(path, identity):
    """Never unlink a pre-existing or replaced path during shutdown."""
    if identity is not None:
        try:
            if owned_identity(path) == identity:
                path.unlink()
        except FileNotFoundError:
            pass


def run_directory(path):
    path = S.directory(path)
    R.BASE.validate_unix_socket_path(path/'b.sock')
    return path


def broker(config, account, run, stop, *, factory=R.FULL.FullPOP, limit=10):
    S.need(type(limit) is int and 1 <= limit <= 10, 'connection_limit_invalid')
    run = run_directory(run)
    S.need(not any(run.iterdir()), 'broker_run_directory_not_empty')
    ipc, ready = run/'b.sock', run/'broker-ready.json'
    socket_id = ready_id = None
    aggregate = R.FULL.C.BrokerAggregate()
    with R.session(config, account) as runtime:
        (run/'broker').mkdir(mode=0o700)  # No reuse or overlapping worker startup.
        log = run/'broker'/'aggregate.txt'
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(ipc))
                socket_id = owned_identity(ipc)
                os.chmod(ipc, 0o600)
                server.listen(1)
                server.settimeout(0.2)
                binding = dict(identity=S.identity(account), namespace=network_namespace())
                S.new_file(ready, (json.dumps(binding)+'\n').encode())
                ready_id = owned_identity(ready)
                R.FULL.C.atomic_write(log, aggregate.text('listening'))
                while not stop.is_set() and aggregate.attempts < limit:
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        if stop.is_set(): break
                        aggregate.attempts += 1
                        runtime.transaction(connection, aggregate, factory=factory)
                    R.FULL.C.atomic_write(log, aggregate.text('listening'))
            R.FULL.C.atomic_write(log, aggregate.text('stopped'))
            return 0
        except Exception:
            R.FULL.C.atomic_write(log, aggregate.text('failed'))
            raise
        finally:
            remove_owned(ready, ready_id)
            remove_owned(ipc, socket_id)


def listener(config, account, run, stop, *, limit=10, server_factory=socket.socket):
    S.need(account in config.accounts, 'unknown_account')
    S.need(type(limit) is int and 1 <= limit <= 10, 'connection_limit_invalid')
    run = run_directory(run)
    binding = json.loads(S.read_private(run/'broker-ready.json', 16384),
                         object_pairs_hook=C.unique_object)
    S.need(type(binding) is dict and set(binding) == {'identity', 'namespace'} and
           binding['identity'] == S.identity(account), 'broker_identity_mismatch')
    isolated_listener(binding['namespace'])
    ipc = run/'b.sock'
    info = ipc.lstat()
    S.need(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.geteuid() and
           stat.S_IMODE(info.st_mode) == 0o600, 'broker_socket_invalid')
    output = run/'listener'
    output.mkdir(mode=0o700)  # Prevent two listeners or log reuse.
    args = SimpleNamespace(socket=ipc, aggregate_log=output/'aggregate.txt',
                           client_timeout=300, max_message_bytes=R.FULL.MAX_MESSAGE)
    aggregate = R.FULL.C.ListenerAggregate()
    ready, ready_id = output/'ready', None
    try:
        with server_factory(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('127.0.0.1', account.listen_port))
            server.listen(4)
            server.settimeout(0.2)
            S.new_file(ready, b'ready=yes\n')
            ready_id = owned_identity(ready)
            R.FULL.C.atomic_write(args.aggregate_log, aggregate.text('listening'))
            while not stop.is_set() and aggregate.connections < limit:
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    if stop.is_set(): break
                    R.FULL.handle_agent_connection(R.BASE, connection, args, aggregate)
            R.FULL.C.atomic_write(args.aggregate_log, aggregate.text('stopped'))
        return 0
    except Exception:
        R.FULL.C.atomic_write(args.aggregate_log, aggregate.text('failed'))
        raise
    finally:
        remove_owned(ready, ready_id)


@contextmanager
def stop_signals(stop):
    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, lambda *_: stop.set())
        yield
    finally:
        for signum, handler in previous.items(): signal.signal(signum, handler)


class RunStop(threading.Event):
    """Finish the current transaction before honoring an outer launcher's stop."""
    def __init__(self, run):
        super().__init__()
        self.marker = run/'stop'

    def is_set(self):
        return super().is_set() or self.marker.exists()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('broker', 'listener'))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--account', required=True)
    parser.add_argument('--run-directory', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        check_user()
        os.umask(0o077)
        S.need(args.config.resolve() == args.config, 'config_path_invalid')
        config = C.parse(S.read_private(args.config, 65536).decode('utf-8'))
        matches = [a for a in config.accounts if a.name == args.account]
        S.need(len(matches) == 1, 'unknown_account')
        stop = RunStop(args.run_directory)
        with stop_signals(stop):
            return (broker if args.role == 'broker' else listener)(
                config, matches[0], args.run_directory, stop)
    except Exception:
        # Never reflect credentials, provider responses, paths or usernames.
        print('BRIDGE-WORKER-STOP: startup or transaction failed; inspect private evidence. No automatic retry.',
              file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
