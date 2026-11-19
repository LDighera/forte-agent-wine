#!/usr/bin/env python3
"""Prepare NEW private desktop settings; no Wine, providers or services started."""
import json
import os
from pathlib import Path
import sys

import account_state as S
import bridge_config as C
import debian_launcher as L
import relay_services as R
import attachment_folders as F
from setup_bridge import check_user, check_destination


def prepare(destination, prefix, wine_root, config_path, services_path=None, browser=True, attachments=None):
    destination = check_destination(destination)
    prefix, wine_root, config_path = map(L.path_checked, (prefix, wine_root, config_path))
    S.need(prefix.is_dir() and prefix.stat().st_uid == os.getuid() and
           not prefix.stat().st_mode & 0o022, 'prefix_not_private_owned')
    S.need((prefix/'drive_c/Program Files (x86)/Agent/agent.exe').is_file(), 'agent_executable_missing')
    for name in ('wine', 'wineserver'):
        S.need(os.access(wine_root/'bin'/name, os.X_OK), 'wine_runtime_missing')
    config = C.parse(S.read_private(config_path, 65536).decode())
    if services_path is not None:
        services_path = L.path_checked(services_path)
        R.load(services_path, config)
    S.need(type(browser) is bool, 'browser_choice_invalid')
    attachments = F.validate([] if attachments is None else attachments, pin=True,
        protected=F.protected_paths(prefix, wine_root, destination, config_path, config, services_path))
    root = destination/'runs'
    S.need(len(os.fsencode(root)) + 12 + max(len(a.name) for a in config.accounts) + 8 < 104,
           'session_path_too_long')
    data = dict(version=2, prefix=str(prefix), wine_root=str(wine_root), config=str(config_path),
                services=str(services_path) if services_path is not None else None,
                session_root=str(root), browser=browser, attachments=attachments)
    return destination, data


def create(plan):
    destination, data = plan
    # No-clobber. A filesystem failure leaves evidence, never a reused setup.
    destination.mkdir(mode=0o700)
    (destination/'runs').mkdir(mode=0o700)
    S.new_file(destination/'desktop.json', (json.dumps(data, indent=2)+'\n').encode())
    S.sync_directory(destination)
    S.sync_directory(destination.parent)


def main():
    check_user()
    os.umask(0o077)
    print('Offline desktop preparation. Agent must be closed. No password or registration code is requested.')
    prefix = input('Existing private Agent Wine prefix (absolute path): ').strip()
    wine = input('Private Wine installation (directory containing bin/wine): ').strip()
    config = input('Prepared POP config.json (absolute path): ').strip()
    services = input('Private relay routes JSON (absolute path, or blank for POP only): ').strip() or None
    browser = input('Enable HTTP(S) links through the Linux desktop portal? [yes/no]: ').strip()
    S.need(browser in ('yes', 'no'), 'explicit_browser_choice_required')
    destination = input('NEW private desktop-settings directory (short absolute path): ').strip()
    count = int(input('Number of existing attachment folders to expose (0-16): '))
    S.need(0 <= count <= 16, 'attachment_count_invalid')
    attachments = []
    for i in range(count):
        folder = input(f'Attachment folder {i+1}, absolute local/mounted path: ').strip()
        mode = input('Mode: ro to attach/read only; rw also permits saving/overwriting files: ').strip()
        attachments.append(dict(path=folder, mode=mode))
    plan = prepare(destination, prefix, wine, config, services, browser == 'yes', attachments)
    print('Will create desktop.json and runs/ in:', plan[0])
    for row in plan[1]['attachments']: print('Folder access:', row['mode'], row['path'])
    print('No Agent settings, accounts or existing files will be changed. No launcher is activated.')
    if input('Type CREATE to prepare these files, or anything else to cancel: ') != 'CREATE':
        print('Cancelled; nothing created.')
        return 0
    create(plan)
    print('Offline desktop settings prepared. Follow DESKTOP.md before creating or using a launcher.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (EOFError, KeyboardInterrupt, OSError, ValueError, RuntimeError):
        print('Desktop setup stopped. Preserve any partial output; do not reuse it or retry automatically.', file=sys.stderr)
        raise SystemExit(1)
