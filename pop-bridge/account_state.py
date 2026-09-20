"""Private per-account state for OFFLINE development; no network or profile edits.

Uses ordinary exclusive creation, fsync and flock. Same-UID malicious processes
are outside this boundary; a production launcher/isolation policy is still needed.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from bridge_config import unique_object

MAX_STATE_BYTES = 64 * 1024 * 1024


class StateError(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise StateError(code)


def directory(path):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path, 'directory_path_invalid')
    st = path.lstat()
    need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and
         stat.S_IMODE(st.st_mode) == 0o700, 'directory_not_private')
    return path


def read_private(path, maximum=MAX_STATE_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and
             st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600,
             'file_not_private_regular')
        need(st.st_size <= maximum, 'state_too_large')
        data = stream.read(maximum + 1)
        need(len(data) <= maximum, 'state_too_large')
        return data


def uids(data):
    values = data.splitlines()
    need(all(0 < len(uid) <= 4096 and all(0x21 <= b <= 0x7e for b in uid)
             for uid in values), 'uid_state_invalid')
    return set(values)


def new_file(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identity(account):
    return dict(version=1, name=account.name, agent_uid=account.agent_uid,
                host=account.host, port=account.port, username=account.username,
                known_uids_file=str(account.known_uids_file))


def initialize(config, account, *, confirm_empty_seed=False):
    """Caller prepares a private state root and closed-profile UID snapshot first.

    Refuses existing account state; interruption leaves an incomplete directory
    for inspection, never implicit overwrite/repair. ready.json is written last.
    """
    try:
        root = directory(config.state_root)
        target = config.account_state(account)
        source = account.known_uids_file
        need(source.resolve() == source, 'seed_path_invalid')
        seed = uids(read_private(source))
        need(seed or confirm_empty_seed is True, 'empty_seed_needs_confirmation')
        canonical = b''.join(uid + b'\n' for uid in sorted(seed))
        target.mkdir(mode=0o700)  # Deliberately NOT exist_ok.
        directory(target)
        new_file(target/'lock', b'')
        new_file(target/'known-uids.dat', canonical)
        new_file(target/'delivered.dat', b'')
        new_file(target/'deferred.dat', b'')
        binding = dict(identity=identity(account),
                       seed_sha256=hashlib.sha256(canonical).hexdigest())
        new_file(target/'ready.json', (json.dumps(binding, sort_keys=True)+'\n').encode())
        sync_directory(target)
        sync_directory(root)
        return target
    except FileExistsError:
        raise StateError('account_state_exists') from None
    except OSError:
        raise StateError('state_initialization_io_failed') from None


@dataclass(frozen=True)
class SessionState:
    path: Path
    known: Path
    delivered: Path
    deferred: Path


@contextmanager
def locked(config, account):
    """Validate the existing account state and hold its exclusive lock throughout."""
    fd = None
    try:
        directory(config.state_root)
        target = directory(config.account_state(account))
        fd = os.open(target/'lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        st = os.fstat(fd)
        need(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and
             st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size == 0,
             'lock_file_invalid')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateError('account_in_use') from None
        try:
            binding = json.loads(read_private(target/'ready.json', 16384),
                                 object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise StateError('state_binding_invalid') from None
        need(type(binding) is dict and set(binding) == {'identity', 'seed_sha256'} and
             binding['identity'] == identity(account), 'account_identity_changed')
        seed = read_private(target/'known-uids.dat')
        need(hashlib.sha256(seed).hexdigest() == binding['seed_sha256'], 'seed_changed')
        uids(seed)
        uids(read_private(target/'delivered.dat'))
        uids(read_private(target/'deferred.dat'))
        stop = target/'PROVIDER-ACCESS-DISABLED'
        need(not stop.exists() and not stop.is_symlink(), 'provider_disabled')
        yield SessionState(target, target/'known-uids.dat', target/'delivered.dat',
                           target/'deferred.dat')
    except OSError:
        raise StateError('state_session_io_failed') from None
    finally:
        if fd is not None:
            os.close(fd)  # Also releases flock, including every failure path.
