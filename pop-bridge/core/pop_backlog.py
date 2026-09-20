#!/usr/bin/env python3
"""Advisory cyclic POP scan position; never a delivered-message ledger.

Numbers are positions within one POP session, not persistent message identities.
Periodic full passes revisit positions after mailbox renumbering. Only the
separate UID ledger establishes delivery. Accepted-run snapshots persist progress.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

UIDS = (1, 3, 7, 8)


def require(ok, code):
    if not ok:
        raise ValueError(code)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_scan_key')
        result[key] = value
    return result


def initial():
    return dict(version=1, next_number=0, passes=0, turn='backlog')


def validate(state):
    require(set(state) == set(initial()), 'scan_fields_invalid')
    require(type(state['version']) is int and state['version'] == 1, 'scan_version_invalid')
    for key in ('next_number', 'passes'):
        require(type(state[key]) is int and 0 <= state[key] < 2**63, 'scan_number_invalid')
    require(state['turn'] in ('head', 'backlog'), 'scan_turn_invalid')
    return state


def read(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024,
            'scan_file_invalid')
    return validate(json.loads(path.read_text(), object_pairs_hook=unique))


def write(path, state):
    data = json.dumps(validate(state), sort_keys=True) + '\n'
    require(not path.is_symlink(), 'scan_symlink_invalid')
    fd, temporary = tempfile.mkstemp(prefix='.scan-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def start(state, count):
    validate(state)
    require(type(count) is int and 0 <= count < 2**63, 'server_count_invalid')
    if state['turn'] == 'head' or state['next_number'] == 0:
        return count
    return min(state['next_number'], count)


def advance(state, next_number):
    result = dict(state)
    if state['turn'] == 'backlog':
        result['next_number'] = next_number
        result['passes'] += int(next_number == 0)
    result['turn'] = 'head' if state['turn'] == 'backlog' else 'backlog'
    return validate(result)


def marker(path):
    require(path.is_file() and not path.is_symlink(), 'current_marker_invalid')
    return unique(line.split('=', 1) for line in path.read_text().splitlines())


def snapshots(current):
    data = marker(current)
    keys = {f'backlog_{uid}_sha256' for uid in UIDS}
    present = keys.intersection(data)
    if not present:
        return {uid: initial() for uid in UIDS}  # Pre-backlog accepted release.
    require(present == keys, 'incomplete_backlog_marker')
    run = Path(data['source_record'])
    require(run.parent == current.parent.parent / 'records/pop-nominal-session-runs'
            and run.is_dir() and not run.is_symlink(), 'backlog_source_invalid')
    result = {}
    for uid in UIDS:
        path = run / f'scan-uid{uid}.after.json'
        result[uid] = read(path)
        require(path.stat().st_uid == 0 and path.stat().st_mode & 0o777 == 0o600,
                'backlog_snapshot_metadata_invalid')
        require(hashlib.sha256(path.read_bytes()).hexdigest() == data[f'backlog_{uid}_sha256'],
                'backlog_snapshot_changed')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('verify', 'stage', 'seal'))
    parser.add_argument('--current', type=Path)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--require-state', action='store_true')
    args = parser.parse_args()
    if args.mode in ('verify', 'stage'):
        states = snapshots(args.current)
        if args.mode == 'stage':
            for uid in UIDS:
                path = args.runtime / f'scan-uid{uid}.json'
                require(not path.exists() and not path.is_symlink(), 'scan_target_exists')
            for uid, state in states.items():
                write(args.runtime / f'scan-uid{uid}.json', state)
    else:
        # Called only after the existing session classifier and UID reconciliation.
        paths = [args.runtime / f'scan-uid{uid}.json' for uid in UIDS]
        if all(not p.exists() and not p.is_symlink() for p in paths):
            require(not args.require_state, 'required_scan_state_missing')
            return  # Legacy session, no scan state to publish.
        states = [read(p) for p in paths]
        for uid in UIDS:
            p = args.run_dir / f'scan-uid{uid}.after.json'
            require(not p.exists() and not p.is_symlink(), 'scan_snapshot_exists')
        for uid, state in zip(UIDS, states):
            write(args.run_dir / f'scan-uid{uid}.after.json', state)


if __name__ == '__main__':
    raise SystemExit('INTERNAL MODULE: use the portable debian_launcher.py entrypoint.')
