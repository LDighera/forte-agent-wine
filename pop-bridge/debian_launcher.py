#!/usr/bin/env python3
"""Private Debian mail launch candidate; not installed or live-approved."""
import argparse
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import account_state as S
import bridge_config as C
from setup_bridge import check_user

PACKAGE = Path(__file__).resolve().parent
CODE = ('debian_launcher.py', 'wine_lifetime.py', 'bridge_worker.py', 'runtime_adapter.py',
        'account_state.py', 'bridge_config.py', 'setup_bridge.py')
CORE = ('pop-full-mail-bridge.py', 'pop-nominal-session-bridge.py',
        'pop-bounded-bridge.py', 'pop_backlog.py', 'pop_catchup_budget.py')


def path_checked(value):
    path = C.absolute_path(str(value))
    S.need(path.resolve() == path, 'path_alias_rejected')
    S.need(path.exists(), 'missing_path')
    # Host paths are retained inside the sandbox for account identity bindings.
    S.need(path.parts[1] in ('home', 'root', 'srv', 'mnt', 'media', 'run', 'opt'),
           'unsupported_mount_location')
    return path


def sandbox(role, config_path, config, account, run, command, *, prefix=None,
            wine_root=None, display=None, xauthority=None):
    """Return argv only. No shell evaluation, installation or host mutation."""
    S.need(role in ('broker', 'client'), 'invalid_role')
    S.need(account in config.accounts, 'unknown_account')
    config_path, run = path_checked(config_path), S.directory(path_checked(run))
    args = ['/usr/bin/bwrap', '--unshare-all', '--cap-drop', 'ALL',
            '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
            '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
            '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
            '--setenv', 'HOME', '/tmp', '--chdir', '/tmp']
    if role == 'broker': args += ['--share-net']
    for system_file in ('/etc/passwd', '/etc/group', '/etc/nsswitch.conf',
                        '/etc/hosts', '/etc/resolv.conf', '/etc/ssl', '/etc/fonts'):
        if Path(system_file).exists(): args += ['--ro-bind', system_file, system_file]
    for name in CODE:
        args += ['--ro-bind', str(PACKAGE/name), '/opt/agent-bridge/'+name]
    for name in CORE:
        args += ['--ro-bind', str(PACKAGE/'core'/name), '/opt/agent-bridge/core/'+name]
    args += ['--ro-bind', str(config_path), str(config_path), '--bind', str(run), str(run)]
    if role == 'broker':
        state = S.directory(path_checked(config.account_state(account)))
        # Runtime validates the state root too; give that empty parent its required mode.
        args += ['--perms', '0700', '--dir', str(config.state_root),
                 '--bind', str(state), str(state)]
    else:
        if prefix is not None:
            prefix = path_checked(prefix)
            S.need(prefix.is_dir(), 'prefix_not_directory')
            args += ['--bind', str(prefix), str(prefix), '--setenv', 'WINEPREFIX', str(prefix),
                     '--setenv', 'WINEARCH', 'wow64', '--setenv', 'WINEDLLOVERRIDES',
                     'mscoree=d;winemenubuilder.exe=d']
        if wine_root is not None:
            wine_root = path_checked(wine_root)
            S.need((wine_root/'bin/wine').is_file(), 'wine_runtime_missing')
            args += ['--ro-bind', str(wine_root), str(wine_root), '--setenv', 'PATH',
                     str(wine_root/'bin')+':/usr/bin:/bin']
        if display is not None:
            S.need(re.fullmatch(r':[0-9]+(?:\.[0-9]+)?', display) is not None,
                   'local_X11_display_required')
            auth = path_checked(xauthority)
            S.need(auth.is_file(), 'xauthority_invalid')
            args += ['--ro-bind', '/tmp/.X11-unix', '/tmp/.X11-unix',
                     '--ro-bind', str(auth), str(auth), '--setenv', 'DISPLAY', display,
                     '--setenv', 'XAUTHORITY', str(auth)]
    return args+['--remount-ro', '/', '--']+list(command)


def client_session(config_path, account, run, application):
    """Compatibility entry point for the existing single-account launcher."""
    return client_sessions(config_path, [(account, run)], application)


def client_sessions(config_path, account_runs, application):
    """Start all listeners before one application in this client's namespace.

    Each account has its own broker/run directory.
    """
    S.need(bool(application), 'application_command_required')
    account_runs = [(account, S.directory(run)) for account, run in account_runs]
    S.need(1 <= len(account_runs) <= 16, 'invalid_account_count')
    for values in ([a.name for a, _ in account_runs],
                   [a.agent_uid for a, _ in account_runs],
                   [a.listen_port for a, _ in account_runs],
                   [run for _, run in account_runs]):
        S.need(len(set(values)) == len(values), 'duplicate_listener_target')
    for _, run in account_runs:
        S.need(not (run/'listener').exists() and not (run/'listener').is_symlink(),
               'listener_directory_already_exists')
    listeners = []
    logs = []
    application_process = None
    requested = False
    previous = {}
    def request_stop(*_):
        nonlocal requested
        requested = True
    def stopping():
        return requested or any((run/'stop').exists() for _, run in account_runs)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, request_stop)
        for account, run in account_runs:
            log = (run/'client-session.log').open('xb')
            logs.append(log)
            worker = ['/usr/bin/python3', '/opt/agent-bridge/bridge_worker.py', 'listener',
                      '--config', str(config_path), '--account', account.name,
                      '--run-directory', str(run)]
            listeners.append(subprocess.Popen(worker, stdout=log, stderr=subprocess.STDOUT,
                                              start_new_session=True))
        deadline = time.monotonic()+15
        while not all((run/'listener/ready').is_file() for _, run in account_runs):
            S.need(all(p.poll() is None for p in listeners) and
                   time.monotonic() < deadline and not stopping(),
                   'listener_not_ready')
            time.sleep(0.05)
        S.need(all(p.poll() is None for p in listeners) and not stopping(),
               'listener_stopped_before_app')
        application_process = subprocess.Popen(application, start_new_session=True)
        warned = False
        while application_process.poll() is None:
            if stopping() or any(p.poll() is not None for p in listeners):
                if not warned:
                    for listener in listeners:
                        if listener.poll() is None: listener.terminate()
                    print('MAIL STOPPED: stop polling; close the application normally. It will not be force-closed.',
                          file=sys.stderr, flush=True)
                    warned = True
            time.sleep(0.2)
        for listener in listeners:
            if listener.poll() is None: listener.terminate()
        # Wait for every listener, even if an earlier one failed.
        returncodes = [listener.wait() for listener in listeners]
        S.need(application_process.returncode == 0 and
               all(code == 0 for code in returncodes) and not warned,
               'client_session_not_accepted')
    finally:
        for listener in listeners:
            if listener.poll() is None: listener.terminate()
        for listener in listeners:
            listener.wait()  # Finish in-flight work; never force-kill commits.
        # If an unexpected error leaves the app open, keep its namespace alive.
        if application_process is not None and application_process.poll() is None:
            print('Close the application normally; no force-close will be sent.', file=sys.stderr)
            application_process.wait()
        for sig, handler in previous.items(): signal.signal(sig, handler)
        for log in logs: log.close()


def wine_application(application, prefix=None, wine_root=None):
    S.need(bool(application), 'application_command_required')
    S.need((prefix is None) == (wine_root is None),
           'prefix_and_wine_root_required_together')
    if wine_root is None: return list(application)
    return ['/usr/bin/python3', '/opt/agent-bridge/wine_lifetime.py',
            '--wine-root', str(wine_root), '--prefix', str(prefix), '--']+list(application)


def launch_session(config_path, config, run, application, **mounts):
    """One outer invocation, separate broker sandboxes, one isolated client.

    Stop files cross the sandbox boundary without terminating Bubblewrap (which
    could tear down Wine or an in-flight commit). No forced close or restart.
    """
    run = S.directory(path_checked(run))
    S.need(not any(run.iterdir()), 'session_directory_not_empty')
    application = wine_application(application, mounts.get('prefix'), mounts.get('wine_root'))
    account_runs = [(a, run/a.name) for a in config.accounts]
    commands = []
    # Construct every command before starting any child.
    for account, child_run in account_runs:
        child_run.mkdir(mode=0o700)
        worker = ['/usr/bin/python3', '/opt/agent-bridge/bridge_worker.py', 'broker',
                  '--config', str(config_path), '--account', account.name,
                  '--run-directory', str(child_run)]
        commands.append(sandbox('broker', config_path, config, account, child_run, worker))
    client_command = sandbox('client', config_path, config, config.accounts[0], run,
        ['/usr/bin/python3', '/opt/agent-bridge/debian_launcher.py', 'inside-client-all',
         '--config', str(config_path), '--run-directory', str(run), '--']+application, **mounts)
    processes, logs, previous = [], [], {}
    client = None
    requested = False
    def request_stop(*_):
        nonlocal requested
        requested = True
    def mark_stop():
        for _, child_run in account_runs:
            try: S.new_file(child_run/'stop', b'stop requested\n')
            except FileExistsError: pass
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, request_stop)
        for (account, _), command in zip(account_runs, commands):
            S.need(not requested, 'session_interrupted_before_ready')
            log = (run/(account.name+'-broker.log')).open('xb')
            logs.append(log)
            processes.append(subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                              start_new_session=True))
        deadline = time.monotonic()+15
        while True:
            S.need(not requested and all(p.poll() is None for p in processes),
                   'broker_stopped_before_client')
            if all((r/'broker-ready.json').is_file() for _, r in account_runs): break
            S.need(time.monotonic() < deadline, 'brokers_not_ready')
            time.sleep(0.05)
        client = subprocess.Popen(client_command, start_new_session=True)
        warned = False
        while client.poll() is None:
            if requested or any(p.poll() is not None for p in processes):
                if not warned:
                    mark_stop()
                    print('SESSION STOP: stop polling and close the application normally; no force-close.',
                          file=sys.stderr, flush=True)
                    warned = True
            time.sleep(0.2)
        mark_stop()
        statuses = [p.wait() for p in processes]
        S.need(client.returncode == 0 and all(code == 0 for code in statuses) and
               not warned and not requested, 'multi_session_not_accepted')
    finally:
        mark_stop()
        # Keep the client namespace until the application closes normally.
        if client is not None and client.poll() is None:
            print('Close the application normally; no force-close will be sent.', file=sys.stderr)
            client.wait()
        for process in processes: process.wait()
        for sig, handler in previous.items(): signal.signal(sig, handler)
        for log in logs: log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('broker', 'client', 'inside-client',
                                        'session', 'inside-client-all'))
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--account')
    parser.add_argument('--run-directory', required=True, type=Path)
    parser.add_argument('--prefix', type=Path)
    parser.add_argument('--wine-root', type=Path)
    parser.add_argument('--display')
    parser.add_argument('--xauthority', type=Path)
    raw = list(sys.argv[1:] if argv is None else argv)
    separator = raw.index('--') if '--' in raw else len(raw)
    application = raw[separator+1:]
    args = parser.parse_args(raw[:separator])
    try:
        check_user()
        os.umask(0o077)
        config = C.parse(S.read_private(args.config, 65536).decode())
        if args.role in ('session', 'inside-client-all'):
            S.need(args.account is None, 'all_accounts_role_disallows_account')
            if args.role == 'inside-client-all':
                client_sessions(args.config, [(a, args.run_directory/a.name) for a in config.accounts],
                                application)
            else:
                launch_session(args.config, config, args.run_directory, application,
                               prefix=args.prefix, wine_root=args.wine_root,
                               display=args.display, xauthority=args.xauthority)
            return 0
        account = next((a for a in config.accounts if a.name == args.account), None)
        S.need(account is not None, 'unknown_account')
        if args.role == 'inside-client':
            client_session(args.config, account, args.run_directory, application)
            return 0
        base = ['--config', str(args.config), '--account', account.name,
                '--run-directory', str(args.run_directory)]
        if args.role == 'broker':
            S.need(not application, 'broker_application_not_allowed')
            command = ['/usr/bin/python3', '/opt/agent-bridge/bridge_worker.py', 'broker']+base
        else:
            application = wine_application(application, args.prefix, args.wine_root)
            command = ['/usr/bin/python3', '/opt/agent-bridge/debian_launcher.py', 'inside-client']+base+['--']+application
        command = sandbox(args.role, args.config, config, account, args.run_directory, command,
                          prefix=args.prefix, wine_root=args.wine_root,
                          display=args.display, xauthority=args.xauthority)
        os.execv(command[0], command)
    except Exception:
        print('DEBIAN-LAUNCH-STOP: startup/session failed; no automatic retry.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
