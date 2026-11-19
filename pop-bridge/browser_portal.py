"""Narrow desktop-bus proxy owned by one Agent session; no browser launch here."""
import os
from pathlib import Path
import socket
import select
import stat
import subprocess

import account_state as S


def command(address, destination):
    # Only a local pathname Unix bus, never TCP, abstract sockets or fallback lists.
    S.need(address.startswith('unix:path=') and ';' not in address and
           '\n' not in address and '\r' not in address, 'desktop_bus_invalid')
    from urllib.parse import unquote
    fields = address[len('unix:'):].split(',')
    S.need(len(fields) <= 2 and (len(fields) == 1 or fields[1].startswith('guid=')), 'desktop_bus_invalid')
    source = Path(unquote(fields[0][len('path='):]))
    S.need(source.is_absolute() and source.resolve() == source and
           stat.S_ISSOCK(source.stat().st_mode) and source.stat().st_uid == os.getuid(), 'desktop_bus_invalid')
    return ['/usr/bin/xdg-dbus-proxy', address, str(destination), '--filter',
            '--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.OpenURI.OpenURI@/org/freedesktop/portal/desktop',
            '--call=org.freedesktop.portal.Desktop=org.freedesktop.portal.Request.Close@/org/freedesktop/portal/desktop/request/*',
            '--broadcast=org.freedesktop.portal.Desktop=org.freedesktop.portal.Request.Response@/org/freedesktop/portal/desktop/request/*']


class Portal:
    def __init__(self, address, root):
        self.destination = root/'.browser-bus'
        S.need(not self.destination.exists() and not self.destination.is_symlink(), 'browser_socket_exists')
        self.argv = command(address, self.destination)
        self.process = None
        self.lifetime = None
        self.stop_requested = False
        self.cleanup_failed = False

    def start(self):
        S.need(self.process is None, 'browser_already_started')
        # Proxy logs never include payloads; no --log option.
        parent, child = socket.socketpair()
        self.lifetime = parent
        try:
            try:
                self.process = subprocess.Popen(self.argv+['--fd='+str(child.fileno())],
                    pass_fds=(child.fileno(),), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
            finally:
                child.close()
            S.need(bool(select.select([parent], [], [], 5)[0]) and parent.recv(1) != b'',
                   'browser_proxy_not_ready')
            st = self.destination.lstat()
            S.need(stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid(), 'browser_socket_invalid')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.lifetime is not None:
            self.lifetime.close()  # Stock proxy --fd lifetime, also closes on launcher death.
            self.lifetime = None
            self.stop_requested = True
        if self.process is not None:
            if self.process.poll() is None and not self.stop_requested:
                self.process.terminate()
                self.stop_requested = True
            try:
                self.process.wait(timeout=5)
                self.process = None
            except subprocess.TimeoutExpired:
                self.cleanup_failed = True
        S.need(not self.cleanup_failed, 'browser_cleanup_failed')
