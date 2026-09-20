#!/usr/bin/env python3
"""Interactive OFFLINE setup; no runtime import, sockets, profile edits or services."""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys

import account_state as S
import bridge_config as C


@dataclass(frozen=True)
class Plan:
    destination: Path
    config: C.Config
    config_bytes: bytes
    seeds: tuple[bytes, ...]


def check_user():
    S.need(os.getuid() != 0 and os.geteuid() == os.getuid(), 'run_as_regular_user_not_sudo')


def check_destination(destination):
    path = C.absolute_path(str(destination))
    S.need(path.resolve() == path, 'destination_symlink_or_alias')
    S.need(not path.exists() and not path.is_symlink(), 'destination_already_exists')
    parent = path.parent.stat()
    S.need(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid() and
           not stat.S_IMODE(parent.st_mode) & 0o022, 'parent_not_owned_or_private_writable')
    return path


def prepare(destination, rows, histories):
    """Read-only plan. None history means an explicitly chosen no-history start.

    Otherwise history is 1..8 private, stable UID snapshots for that mailbox.
    Caller must establish completeness; this cannot discover the right profile.
    """
    destination = check_destination(destination)
    S.need(type(rows) is list and type(histories) is list and
           1 <= len(rows) == len(histories) <= 16, 'account_count_invalid')
    fields = {'name', 'agent_uid', 'host', 'port', 'username', 'listen_port'}
    S.need(all(type(row) is dict and set(row) == fields for row in rows), 'setup_fields_invalid')
    # Validate names before using them as path components, through the existing parser.
    provisional = dict(version=1, state_root=str(destination/'state'), accounts=[
        dict(row, known_uids_file=str(destination/'history'/f'{i}.dat'))
        for i, row in enumerate(rows)])
    C.parse(json.dumps(provisional))
    for row in provisional['accounts']:
        row['known_uids_file'] = str(destination/'history'/(row['name']+'.dat'))
    config_bytes = (json.dumps(provisional, indent=2)+'\n').encode('utf-8')
    config = C.parse(config_bytes.decode('utf-8'))
    seeds, total_read = [], 0
    for sources in histories:
        if sources is None:
            seeds.append(b'')
            continue
        S.need(type(sources) is list and 1 <= len(sources) <= 8, 'history_sources_invalid')
        known = set()
        for source in sources:
            source = C.absolute_path(str(source))
            S.need(source.resolve() == source, 'seed_path_invalid')
            data = S.read_private(source, S.MAX_STATE_BYTES-total_read)
            total_read += len(data)
            known.update(S.uids(data))
        S.need(bool(known), 'empty_import_refused_choose_no_history_explicitly')
        seeds.append(b''.join(uid+b'\n' for uid in sorted(known)))
    return Plan(destination, config, config_bytes, tuple(seeds))


def create(plan):
    """New directory only. On failure retain partial output, never repair/retry."""
    destination = check_destination(plan.destination)
    destination.mkdir(mode=0o700)
    S.directory(destination)
    history = destination/'history'
    history.mkdir(mode=0o700)
    plan.config.state_root.mkdir(mode=0o700)
    for account, seed in zip(plan.config.accounts, plan.seeds, strict=True):
        S.new_file(account.known_uids_file, seed)
        S.initialize(plan.config, account, confirm_empty_seed=(seed == b''))
        with S.locked(plan.config, account): pass
    S.sync_directory(history)
    # Config is last: partial account initialization must not produce usable config.
    S.new_file(destination/'config.json', plan.config_bytes)
    S.sync_directory(destination)
    S.sync_directory(destination.parent)
    return destination/'config.json'


def collect(ask, say):
    say('OFFLINE POP BRIDGE SETUP — PRIVATE DEVELOPMENT, NOT A LIVE INSTALLER')
    say('No password is requested. No connection, service, or Agent profile change occurs.')
    say('Use a NEW absolute directory under an existing directory you own.')
    destination = ask('New setup directory: ').strip()
    count = int(ask('Number of accounts (1-16): '))
    S.need(1 <= count <= 16, 'account_count_invalid')
    rows, histories = [], []
    for i in range(count):
        say(f'Account {i+1} of {count}: use its actual inbound Agent account UID, not its list position.')
        rows.append(dict(
            name=ask('Short stable name (lowercase letters, digits, _ or -): ').strip(),
            agent_uid=int(ask('Agent account UID: ')),
            host=ask('Provider POP hostname (not a URL): ').strip(),
            port=int(ask('Implicit TLS port [995]: ').strip() or '995'),
            username=ask('Provider login name (NOT password): ').strip(),
            listen_port=int(ask('Unique local port (1024-65535): ')),
        ))
        mode = ask('History mode: type import or new: ').strip()
        S.need(mode in ('import', 'new'), 'history_mode_invalid')
        if mode == 'new':
            say('No delivery history means existing server mail may download again later.')
            S.need(ask('Confirm by typing START WITHOUT HISTORY: ') == 'START WITHOUT HISTORY',
                   'no_history_not_confirmed')
            histories.append(None)
        else:
            say('Use closed-profile snapshots: newline-separated POP UIDs, NOT message .DAT/.IDX files.')
            say('Include Agent UID history AND any previous bridge committed-delivery ledger.')
            say('Each snapshot must be your own private 0600 regular file, not a link.')
            n = int(ask('Number of history snapshots for this account (1-8): '))
            S.need(1 <= n <= 8, 'history_sources_invalid')
            sources = [ask(f'Absolute snapshot path {j+1}: ').strip() for j in range(n)]
            S.need(ask('Confirm closed source and complete history: type IMPORT COMPLETE HISTORY: ')
                   == 'IMPORT COMPLETE HISTORY', 'history_not_confirmed')
            histories.append(sources)
    return prepare(destination, rows, histories)


def main(argv=None, *, ask=input, say=print):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        check_user()
        plan = collect(ask, say)
        say('Destination: '+json.dumps(str(plan.destination)))
        for account, seed in zip(plan.config.accounts, plan.seeds, strict=True):
            say(f'{account.name}: Agent UID {account.agent_uid}; login '
                f'{json.dumps(account.username)}; {account.host}:{account.port}; local port '
                f'{account.listen_port}; {len(seed.splitlines())} known message IDs.')
        say('Creates private configuration/history/state only; no services or live mail.')
        S.need(ask('Type CREATE OFFLINE SETUP to write, or anything else to cancel: ')
               == 'CREATE OFFLINE SETUP', 'creation_not_confirmed')
        create(plan)
        say('OFFLINE SETUP CREATED. Not installed, activated, or connected. Keep this directory private.')
        return 0
    except (C.ConfigError, S.StateError) as exc:
        say('SETUP STOP: '+str(exc))
    except (EOFError, KeyboardInterrupt):
        say('SETUP STOP: canceled')
    except (OSError, ValueError, RuntimeError):
        say('SETUP STOP: invalid input or local filesystem failure')
    say('Nothing was activated. If a destination directory was created, retain it for inspection; do not reuse it.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
