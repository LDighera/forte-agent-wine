"""Private test bus only. Does not open URLs or read a desktop session bus."""
import sys
from gi.repository import Gio, GLib

conn = Gio.DBusConnection.new_for_address_sync(sys.argv[1],
    Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
xml = '''<node><interface name="org.freedesktop.portal.OpenURI">
<method name="OpenURI"><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Forbidden"/></interface>
<interface name="org.freedesktop.portal.Secret"><method name="RetrieveSecret"/></interface></node>'''
def call(connection, sender, path, iface, method, params, invocation):
    if method == 'OpenURI':
        url = params.unpack()[1]
        assert url in ('https://example.invalid/synthetic?quote=%22&literal=$nothing', 'https://example.invalid/cancel')
        handle = '/org/freedesktop/portal/desktop/request/'+sender[1:].replace('.', '_')+'/'+params.unpack()[2]['handle_token']
        print('OPENURI', flush=True)
        invocation.return_value(GLib.Variant('(o)', (handle,)))
        def complete():
            conn.emit_signal(sender, handle, 'org.freedesktop.portal.Request', 'Response',
                GLib.Variant('(ua{sv})', (1 if url.endswith('/cancel') else 0, {})))
            return False
        GLib.timeout_add(30, complete)
    else:
        # If the proxy leaked permission this would be a successful call.
        invocation.return_value(GLib.Variant('()', ()))
for interface in Gio.DBusNodeInfo.new_for_xml(xml).interfaces:
    conn.register_object('/org/freedesktop/portal/desktop', interface, call, None, None)
conn.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
    'RequestName', GLib.Variant('(su)', ('org.freedesktop.portal.Desktop', 0)),
    GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
print('READY', flush=True)
GLib.MainLoop().run()
