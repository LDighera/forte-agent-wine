"""Offline configuration boundary for the portable POP development copy.

No credential store, profile parsing, file mutation or provider connection.
Runtime activation and state ownership validation are deliberately not supplied.
"""
from dataclasses import dataclass
import json
from pathlib import Path
import re


class ConfigError(ValueError):
    """Fixed diagnostic codes only: do not echo configuration values."""


def require(condition, code):
    if not condition:
        raise ConfigError(code)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_key')
        result[key] = value
    return result


def absolute_path(value):
    require(isinstance(value, str) and '\x00' not in value, 'path_invalid')
    path = Path(value)
    require(path.is_absolute() and '..' not in path.parts and path != Path('/'),
            'path_invalid')
    return path


@dataclass(frozen=True)
class Account:
    name: str
    agent_uid: int
    host: str
    port: int
    username: str
    listen_port: int
    known_uids_file: Path

    def as_bridge_account(self, base):
        """Use the unchanged broker Account shape; password still arrives from Agent."""
        return base.Account(self.agent_uid, self.host, self.port,
                            self.username.encode('ascii'))


@dataclass(frozen=True)
class Config:
    state_root: Path
    accounts: tuple[Account, ...]

    def account_state(self, account):
        require(account in self.accounts, 'unknown_account')
        return self.state_root / account.name


def parse(text):
    require(isinstance(text, str) and len(text.encode('utf-8')) <= 65536,
            'config_size_invalid')
    try:
        data = json.loads(text, object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, ConfigError):
            raise
        raise ConfigError('invalid_json') from None
    require(type(data) is dict and set(data) == {'version', 'state_root', 'accounts'},
            'config_fields_invalid')
    require(type(data['version']) is int and data['version'] == 1, 'version_invalid')
    state_root = absolute_path(data['state_root'])
    require(type(data['accounts']) is list and 1 <= len(data['accounts']) <= 16,
            'accounts_invalid')
    accounts = []
    for item in data['accounts']:
        require(type(item) is dict and set(item) == {
            'name', 'agent_uid', 'host', 'port', 'username', 'listen_port', 'known_uids_file'
        }, 'account_fields_invalid')
        name, host, username = item['name'], item['host'], item['username']
        require(isinstance(name, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', name),
                'account_name_invalid')
        require(type(item['agent_uid']) is int and 1 <= item['agent_uid'] < 2**31,
                'agent_uid_invalid')
        require(isinstance(host, str) and 1 <= len(host) <= 253 and all(
            re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
            for label in host.split('.')), 'host_invalid')
        require(type(item['port']) is int and 1 <= item['port'] <= 65535, 'port_invalid')
        require(type(item['listen_port']) is int and 1024 <= item['listen_port'] <= 65535,
                'listen_port_invalid')
        require(isinstance(username, str) and 1 <= len(username) <= 4096 and
                username == username.strip() and all(0x20 <= ord(c) <= 0x7e for c in username),
                'username_invalid')
        accounts.append(Account(name, item['agent_uid'], host.lower(), item['port'],
                                username, item['listen_port'], absolute_path(item['known_uids_file'])))
    for values in (
        [a.name for a in accounts], [a.agent_uid for a in accounts],
        [a.listen_port for a in accounts], [a.known_uids_file for a in accounts],
        [(a.host, a.port, a.username) for a in accounts],
    ):
        require(len(values) == len(set(values)), 'duplicate_account_mapping')
    return Config(state_root, tuple(accounts))


def load(path):
    # Only an offline convenience reader, NOT runtime permission/ownership validation.
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'config_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, 'config_size_invalid')
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        raise ConfigError('invalid_utf8') from None
    return parse(text)
