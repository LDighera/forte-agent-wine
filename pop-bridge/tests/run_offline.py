#!/usr/bin/env python3
"""Run copied synthetic tests from a private short path; no profile/config import."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) != 1:
        raise SystemExit('Usage: python3 pop-bridge/tests/run_offline.py')
    # The sandbox-argument tests intentionally exercise supported home paths.
    # A short path also avoids AF_UNIX's platform socket-path length limit.
    home = Path.home().resolve()
    if home.parts[1] not in ('home', 'root', 'srv', 'mnt', 'media', 'run', 'opt'):
        raise SystemExit('Run tests with an ordinary home directory in a supported mount location.')
    if len(os.fsencode(home)) > 42:
        raise SystemExit('The home path is too long for these Unix-socket fixtures.')
    with tempfile.TemporaryDirectory(prefix='.agt-', dir=home) as scratch:
        work = Path(scratch)
        # Copy source only; never copy config.json, state, profiles or records.
        for directory in ('', 'core', 'tests', 'tests/support'):
            destination = work / directory
            destination.mkdir(exist_ok=True)
            for source in (ROOT / directory).glob('*.py'):
                if source.is_symlink() or not source.is_file():
                    raise SystemExit('Unexpected source-file type; tests stopped.')
                shutil.copyfile(source, destination / source.name)
        example = ROOT / 'config.example.json'
        if example.is_symlink():
            raise SystemExit('Example configuration must be a regular source file.')
        raw = example.read_bytes()
        accounts = json.loads(raw)['accounts']
        if not accounts or any(not a['host'].endswith('.example.invalid') or
                               a['username'] not in ('reader@example.invalid', 'reader@second.example.invalid')
                               for a in accounts):
            raise SystemExit('Example configuration is no longer the synthetic fixture; tests stopped.')
        (work / 'config.example.json').write_bytes(raw)
        (work / 'records').mkdir(mode=0o700)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
        env.pop('PYTHONPATH', None)
        print('Synthetic tests only: temporary state, fake providers, Unix sockets. No Agent launch.', flush=True)
        result = subprocess.run([sys.executable, '-B', '-s', '-m', 'unittest',
                                 'discover', '-s', 'tests'], cwd=work, env=env)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
