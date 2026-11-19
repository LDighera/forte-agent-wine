#!/usr/bin/env python3
"""Explicit offline probe with a NEW private fixture directory. No real media."""
from pathlib import Path
import os
import socket
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import attachment_folders as F
import account_state as S

os.umask(0o077)
S.need({name for _,name in socket.if_nameindex()}=={'lo'},'offline_namespace_required')
root=S.directory(Path(sys.argv[1]))
S.need(not any(root.iterdir()),'empty_fixture_required')
media=root/'media';media.mkdir()
(media/'input.txt').write_text('synthetic attachment')
for mode in ('ro','rw'):
    row,=F.validate([dict(path=str(media),mode=mode)],protected=[],pin=True)
    F.validate([row],protected=[])
    args=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--cap-drop','ALL',
          '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
          '--symlink','usr/lib64','/lib64','--dev','/dev','--tmpfs','/tmp',
          '--ro-bind' if mode=='ro' else '--bind',str(media),str(media),
          '--remount-ro','/','--','/usr/bin/python3','-c',
          "from pathlib import Path;import sys,errno;p=Path(sys.argv[2]);assert (p/'input.txt').read_text()=='synthetic attachment';\n"
          "try:(p/'output.txt').write_text('saved')\n"
          "except OSError as e:assert sys.argv[1]=='ro' and e.errno==errno.EROFS\n"
          "else:assert sys.argv[1]=='rw'",mode,str(media)]
    r=subprocess.run(args,capture_output=True,timeout=15)
    (root/(mode+'.stderr')).write_bytes(r.stderr)
    S.need(r.returncode==0,'attachment_probe_failed')
S.need((media/'output.txt').read_text()=='saved','save_missing')
print('PASS: selected Linux path readable; RO prevents save; RW saves synthetic file.')
private=root/'private';private.mkdir()
alias=root/'alias';alias.mkdir()
parent=root/'parent';parent.mkdir()
nested=parent/'nested';nested.mkdir()
source=Path(__file__).resolve().parents[1]
for selected,destination,expected in [(alias,alias,'alias_overlap'),(parent,nested,'nested_mount')]:
    code="""from pathlib import Path
import sys
sys.path.insert(0,'/code')
import attachment_folders as F
import account_state as S
try: F.validate([dict(path=sys.argv[1],mode='ro')],protected=[Path(sys.argv[2])],pin=True)
except S.StateError as e: assert sys.argv[3] in str(e), str(e)
else: raise AssertionError('private alias exposed')
"""
    args=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--cap-drop','ALL',
          '--ro-bind','/usr','/usr','--symlink','usr/bin','/bin','--symlink','usr/lib','/lib',
          '--symlink','usr/lib64','/lib64','--dev','/dev','--proc','/proc','--tmpfs','/tmp',
          '--ro-bind',str(source),'/code','--bind',str(root),str(root),
          '--ro-bind',str(private),str(destination),'--remount-ro','/','--',
          '/usr/bin/python3','-B','-c',code,str(selected),str(private),expected]
    r=subprocess.run(args,capture_output=True,timeout=15)
    (root/(expected+'.stderr')).write_bytes(r.stderr)
    S.need(r.returncode==0,'attachment_alias_probe_failed')
print('PASS: actual private-directory bind alias and nested mount both rejected.')
