#!/usr/bin/python3
"""Wine's xdg-open replacement: HTTP(S) through the desktop OpenURI portal.

Portal completion confirms handoff, not browser display or page loading.
No URL, which may contain private tokens, is written to diagnostic output.
"""
import sys
import os
import secrets
from urllib.parse import urlsplit


def validate(url):
    if not isinstance(url, str) or not 1 <= len(url) <= 8192:
        raise ValueError('unsupported_link')
    if any(ord(c) <= 32 or ord(c) == 127 for c in url) or '\\' in url:
        raise ValueError('unsupported_link')
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname or parsed.username is not None:
        raise ValueError('unsupported_link')
    _ = parsed.port
    return url


def open_url(url, timeout_ms=30000):
    url = validate(url)
    from gi.repository import Gio, GLib
    bus = Gio.DBusConnection.new_for_address_sync(os.environ['DBUS_SESSION_BUS_ADDRESS'],
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None, None)
    token = 'agent'+secrets.token_hex(8)
    handle = '/org/freedesktop/portal/desktop/request/'+bus.get_unique_name()[1:].replace('.', '_')+'/'+token
    expected = [handle]
    result = []
    loop = GLib.MainLoop()
    def response(connection, sender, path, interface, signal, params, *unused):
        if path == expected[0]:
            result.append(params.unpack()[0])
            loop.quit()
    subscription = bus.signal_subscribe('org.freedesktop.portal.Desktop', 'org.freedesktop.portal.Request',
        'Response', None, None, Gio.DBusSignalFlags.NONE, response)
    timer = None
    try:
        reply = bus.call_sync('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop',
            'org.freedesktop.portal.OpenURI', 'OpenURI',
            GLib.Variant('(ssa{sv})', ('', url, {'handle_token': GLib.Variant('s', token)})),
            GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 15000, None)
        expected[0] = reply.unpack()[0]
        def expired():
            result.append(2)
            loop.quit()
            return False
        timer = GLib.timeout_add(timeout_ms, expired)
        loop.run()
        if result != [0]:
            try:
                bus.call_sync('org.freedesktop.portal.Desktop', expected[0], 'org.freedesktop.portal.Request',
                    'Close', None, None, Gio.DBusCallFlags.NONE, 1000, None)
            except GLib.Error:
                pass
            raise RuntimeError('portal_cancelled_or_failed')
    finally:
        if timer is not None and GLib.MainContext.default().find_source_by_id(timer) is not None:
            GLib.source_remove(timer)
        bus.signal_unsubscribe(subscription)
        bus.close_sync(None)


def main():
    try:
        if len(sys.argv) != 2: raise ValueError('one_link_required')
        open_url(sys.argv[1])
        return 0
    except Exception:
        print('Agent link was not handed to the browser. Copy the HTTP(S) link into your browser.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
