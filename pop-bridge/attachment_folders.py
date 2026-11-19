"""Explicit host attachment folders; no scanning, copying or automatic mounts.

Filesystem identity checking follows this project's portable PSP9 runner.
"""
import json
import os
from pathlib import Path
import subprocess

import account_state as S
import bridge_config as C


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def identity(folder):
    r = subprocess.run(['/usr/bin/findmnt', '--json', '--target', str(folder),
        '--output', 'TARGET,SOURCE,FSTYPE,FSROOT,UUID'], capture_output=True, text=True, timeout=10)
    S.need(r.returncode == 0, 'attachment_mount_unavailable')
    rows = json.loads(r.stdout)['filesystems']
    S.need(len(rows) == 1, 'attachment_mount_ambiguous')
    row = rows[0]
    S.need(all(isinstance(row.get(k), str) and row[k] for k in ('target','source','fstype','fsroot')),
           'attachment_mount_invalid')
    return dict(target=row['target'], filesystem=row['fstype'], root=row['fsroot'],
                identity='uuid:'+row['uuid'] if row.get('uuid') else 'source:'+row['source'])


def mount_points():
    r = subprocess.run(['/usr/bin/findmnt','--json','--list','--output','TARGET'],
                       capture_output=True,text=True,timeout=10)
    S.need(r.returncode == 0, 'attachment_mount_unavailable')
    return [Path(row['target']) for row in json.loads(r.stdout)['filesystems']]


def location(path, mount=None):
    # Kernel filesystem root + mount-relative path also identifies bind aliases.
    path = path.resolve()
    existing = path
    while not existing.exists(): existing = existing.parent
    mount = identity(existing) if mount is None else mount
    target = Path(mount['target'])
    S.need(path == target or target in path.parents, 'attachment_mount_invalid')
    return existing.stat().st_dev, Path(mount['root']) / path.relative_to(target)


def validate(rows, *, protected, home=None, pin=False):
    S.need(type(rows) is list and len(rows) <= 16, 'attachment_count_invalid')
    home = Path.home().resolve() if home is None else home
    reserved = [Path(x) for x in ('/usr','/etc','/proc','/sys','/dev','/run','/opt',
        '/bin','/sbin','/lib','/lib64','/tmp/.X11-unix','/tmp/agent-browser-bus')]
    reserved += list(protected)
    paths, result = [], []
    mounts = mount_points() if rows else []
    protected_locations = [location(item) for item in reserved] if rows else []
    home_location = location(home) if rows else None
    for row in rows:
        S.need(type(row) is dict and set(row) == ({'path','mode'} if pin else {'path','mode','mount'}),
               'attachment_fields_invalid')
        p = C.absolute_path(row['path'])
        S.need(not any(ord(c)<32 or ord(c)==127 for c in str(p)), 'attachment_path_invalid')
        S.need(p.resolve() == p and p.is_dir(), 'attachment_path_unavailable')
        S.need(len(p.parts) >= 3 and p.parts[1] in ('home','root','srv','mnt','media','tmp'),
               'attachment_location_invalid')
        S.need(p != home and p not in home.parents, 'attachment_entire_home_disallowed')
        if home in p.parents:
            S.need(not p.relative_to(home).parts[0].startswith('.'), 'attachment_private_home_disallowed')
        S.need(not any(overlaps(p, item) for item in reserved+paths), 'attachment_path_overlap')
        S.need(row['mode'] in ('ro','rw'), 'attachment_mode_invalid')
        S.need(os.access(p, os.R_OK | os.X_OK | (os.W_OK if row['mode']=='rw' else 0)),
               'attachment_permission_denied')
        actual = identity(p)
        S.need(not any(p in item.parents for item in mounts), 'attachment_nested_mount_disallowed')
        device, physical = location(p, actual)
        home_device, home_path = home_location
        if device == home_device:
            S.need(physical != home_path and physical not in home_path.parents,
                   'attachment_entire_home_disallowed')
            if home_path in physical.parents:
                S.need(not physical.relative_to(home_path).parts[0].startswith('.'),
                       'attachment_private_home_disallowed')
        S.need(not any(device == d and overlaps(physical, other) for d,other in protected_locations),
               'attachment_mount_alias_overlap')
        if not pin: S.need(row['mount'] == actual, 'attachment_mount_changed')
        result.append(dict(path=str(p), mode=row['mode'], mount=actual))
        paths.append(p)
    return result


def protected_paths(prefix, wine_root, settings_dir, config_path, config, services_path=None):
    result = [prefix, wine_root, settings_dir, config_path, config.state_root,
              Path(__file__).resolve().parent.parent,
              Path(os.environ.get('XAUTHORITY', str(Path.home()/'.Xauthority'))).resolve()]
    result.extend(a.known_uids_file for a in config.accounts)
    if services_path is not None: result.append(services_path)
    return result
