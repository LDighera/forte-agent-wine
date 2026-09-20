#!/usr/bin/env python3
"""Keep the client sandbox alive through Wine and its prefix-specific server wait."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys

import account_state as S
from setup_bridge import check_user


def run(wine_root, prefix, application):
    check_user()
    S.need(bool(application), 'windows_application_required')
    S.need(prefix.is_absolute() and prefix.resolve() == prefix and prefix.is_dir(),
           'prefix_invalid')
    S.need(prefix.stat().st_uid == os.geteuid(), 'prefix_owner_invalid')
    S.need(os.environ.get('WINEPREFIX') == str(prefix), 'prefix_environment_mismatch')
    wine, server = wine_root/'bin/wine', wine_root/'bin/wineserver'
    S.need(wine_root.is_absolute() and wine_root.resolve() == wine_root and
           wine.is_file() and server.is_file() and os.access(wine, os.X_OK) and
           os.access(server, os.X_OK), 'wine_runtime_invalid')
    previous = {}
    def stop_requested(*_):
        print('Close Agent normally. Waiting for this private Wine prefix; no force-close will be sent.',
              file=sys.stderr, flush=True)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, stop_requested)
        process = subprocess.Popen([str(wine)]+list(application), start_new_session=True)
        try:
            wine_status = process.wait()
        finally:
            # Do not skip server wait merely because wine failed or returned early.
            process.wait()
            server_status = subprocess.run([str(server), '-w'], check=False,
                                           start_new_session=True).returncode
        S.need(wine_status == 0 and server_status == 0, 'wine_or_server_exit_failed')
        return 0
    finally:
        for sig, handler in previous.items(): signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wine-root', required=True, type=Path)
    parser.add_argument('--prefix', required=True, type=Path)
    raw = list(sys.argv[1:] if argv is None else argv)
    separator = raw.index('--') if '--' in raw else len(raw)
    args = parser.parse_args(raw[:separator])
    try:
        return run(args.wine_root, args.prefix, raw[separator+1:])
    except Exception:
        print('WINE-LIFETIME-STOP: startup or normal-exit verification failed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
