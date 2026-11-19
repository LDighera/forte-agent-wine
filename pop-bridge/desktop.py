#!/usr/bin/env python3
"""Private, terminal-free frontend for the complete portable Agent session.

Only a previously completed normal session can be recovered automatically.
Failed mail transactions and incomplete sessions remain blocked for inspection.
No mailbox repair, ledger clearing, automatic poll, resend or forced app close.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import account_state as S
import bridge_config as C
import debian_launcher as L
import relay_services as R
import browser_portal as B
import attachment_folders as F
from setup_bridge import check_user

PACKAGE = Path(__file__).resolve().parent


def load(path):
    path = L.path_checked(path)
    raw = S.read_private(path, 16384)
    try:
        data = json.loads(raw, object_pairs_hook=C.unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise S.StateError('desktop_settings_invalid') from None
    fields = {'version', 'prefix', 'wine_root', 'config', 'services', 'session_root', 'browser'}
    S.need(type(data) is dict and type(data.get('version')) is int and
           ((data['version'] == 1 and set(data) == fields) or
            (data['version'] == 2 and set(data) == fields | {'attachments'})) and
           type(data['browser']) is bool, 'desktop_fields_invalid')
    for field in ('prefix', 'wine_root', 'config', 'session_root'):
        data[field] = L.path_checked(data[field])
    data['services'] = L.path_checked(data['services']) if data['services'] is not None else None
    S.directory(data['session_root'])
    prefix = data['prefix']
    S.need(prefix.is_dir() and prefix.stat().st_uid == os.getuid(), 'prefix_owner_invalid')
    S.need(not (prefix.stat().st_mode & 0o022), 'prefix_permissions_invalid')
    data['executable'] = prefix/'drive_c/Program Files (x86)/Agent/agent.exe'
    S.need(data['executable'].is_file(), 'agent_executable_missing')
    for name in ('wine', 'wineserver'):
        S.need(os.access(data['wine_root']/'bin'/name, os.X_OK), 'wine_runtime_missing')
    config = C.parse(S.read_private(data['config'], 65536).decode())
    if data['services'] is not None:
        R.load(data['services'], config)
    data['attachments'] = F.validate(data.get('attachments', []), protected=F.protected_paths(
        prefix, data['wine_root'], path.parent, data['config'], config, data['services']))
    # Pin this session to exact private settings, including account/route mappings.
    digest = hashlib.sha256(raw + S.read_private(data['config'], 65536) +
        (S.read_private(data['services'], 16384) if data['services'] is not None else b'')).hexdigest()
    S.need(len(os.fsencode(data['session_root'])) + 12 +
           max(len(a.name) for a in config.accounts) + 8 < 104, 'session_path_too_long')
    return data, config, digest


@contextmanager
def lock(prefix):
    # A common lock in the prefix prevents different desktop settings from
    # concurrently launching the same profile through this package.
    fd = os.open(prefix/'.portable-agent.lock', os.O_RDWR | os.O_CREAT |
                 os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        S.need(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
               st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size == 0,
               'launcher_lock_invalid')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise S.StateError('agent_already_running') from None
        yield
    finally:
        os.close(fd)


def recover_completed(prefix, root, digest):
    pending = prefix/'.portable-agent.pending'
    if not pending.exists() and not pending.is_symlink():
        return False
    raw = S.read_private(pending, 4096)
    try:
        record = json.loads(raw, object_pairs_hook=C.unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise S.StateError('session_record_invalid') from None
    S.need(type(record) is dict and set(record) == {'run', 'settings_sha256'} and
           record['settings_sha256'] == digest, 'session_requires_review')
    run = C.absolute_path(record['run'])
    S.need(run.parent == root and run.name.startswith('run-'), 'session_record_invalid')
    S.directory(run)
    # Only our successful finalization record permits cleanup. A failed or
    # terminated session has no such record and cannot be silently restarted.
    S.need(S.read_private(run/'completed.json', 4096) == raw, 'session_requires_review')
    pending.unlink()
    S.sync_directory(prefix)
    return True


def notice(message):
    if Path('/usr/bin/kdialog').is_file():
        subprocess.run(['/usr/bin/kdialog', '--title', 'Forté Agent', '--error', message], check=False)
    else:
        print(message, file=sys.stderr)


def run(settings):
    check_user()
    os.umask(0o077)
    data, config, digest = load(settings)
    prefix, root = data['prefix'], data['session_root']
    with lock(prefix):
        recover_completed(prefix, root, digest)
        # Preflight account locks/stop markers before any network-capable child.
        for account in config.accounts:
            with S.locked(config, account):
                pass
        display = os.environ.get('DISPLAY', '')
        authority = Path(os.environ.get('XAUTHORITY', str(Path.home()/'.Xauthority')))
        run_dir = Path(tempfile.mkdtemp(prefix='run-', dir=root))
        portal = B.Portal(os.environ.get('DBUS_SESSION_BUS_ADDRESS', ''), run_dir) if data['browser'] else None
        mounts = dict(prefix=prefix, wine_root=data['wine_root'], display=display,
                      xauthority=authority, services_path=data['services'], attachment_folders=data['attachments'])
        # Verify the desktop and filesystem arguments without launching anything.
        # The complete launcher creates its private hosts file before execution.
        L.sandbox('client', data['config'], config, config.accounts[0], run_dir,
                  ['/usr/bin/true'], **mounts)
        pending = prefix/'.portable-agent.pending'
        record = (json.dumps(dict(run=str(run_dir), settings_sha256=digest), sort_keys=True)+'\n').encode()
        S.new_file(pending, record)
        S.sync_directory(prefix)
        # Synchronous coordination retains the prefix lock through the Wine server
        # wait and every broker/relay shutdown, including failure paths.
        try:
            if portal is not None:
                portal.start()
                mounts['browser_socket'] = portal.destination
            L.launch_session(data['config'], config, run_dir, [str(data['executable'])],
                             notify=notice, **mounts)
        finally:
            if portal is not None: portal.close()
        S.new_file(run_dir/'completed.json', record)
        S.sync_directory(run_dir)
        recover_completed(prefix, root, digest)
        return 0


def quote(value):
    S.need(not any(ord(c) < 32 for c in value), 'desktop_path_invalid')
    value = value.replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\'+character)
    return ('"'+value+'"').replace('\\', '\\\\')


def create_entry(settings, output):
    check_user()
    load(settings)
    output = C.absolute_path(str(output))
    S.need(output.resolve() == output and output.suffix == '.desktop' and
           output.parent.is_dir(), 'desktop_output_invalid')
    S.need(Path('/usr/bin/kdialog').is_file() and
           Path('/usr/bin/desktop-file-validate').is_file(), 'desktop_tools_missing')
    content = ('[Desktop Entry]\nType=Application\nName=Forté Agent\n'
        'Comment=Private Wine mail and news session\nTerminal=false\n'
        'Icon=internet-mail\nCategories=Network;Email;News;\nExec=' +
        ' '.join(quote(s) for s in ['/usr/bin/python3', '-B', str(PACKAGE/'desktop.py'),
            'launch', '--settings', str(settings)])+'\n')
    S.new_file(output, content.encode())
    result = subprocess.run(['/usr/bin/desktop-file-validate', str(output)], check=False)
    S.need(result.returncode == 0, 'desktop_entry_validation_failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('launch', 'check', 'create-entry'))
    parser.add_argument('--settings', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        check_user()
        if args.operation == 'create-entry':
            S.need(args.output is not None, 'desktop_output_required')
            create_entry(args.settings, args.output)
        elif args.operation == 'check':
            load(args.settings)
            print('Private desktop settings validated; nothing launched.')
        else:
            return run(args.settings)
        return 0
    except (OSError, ValueError, RuntimeError):
        # Fixed text avoids reflecting credentials or URLs through exception text.
        notice('Agent could not start or complete normally. No automatic retry was made. '
               'Close an open Agent normally. Preserve the private session records; '
               'do not delete pending-session or provider-stop files. '
               'See the package recovery instructions.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
