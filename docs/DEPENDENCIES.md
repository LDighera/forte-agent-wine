# 1. Contents and licensing boundaries

This archive contains this project's Python and shell source, tests, documentation,
example configuration and MIT license. It contains no third-party Python packages,
Wine source or binaries, Agent installer, private profile, registration information,
mail or credentials. Project source and documentation were developed with Codex;
the README records that contribution. The MIT license covers the project files,
not separately obtained dependencies.

Wine and its upstream input correction are downloaded by the build recipe. Keep
their upstream licensing and notices with any separately built redistribution.
This source archive does not redistribute those downloads. Agent must be obtained
and licensed by the user. No registration bypass is supplied.

# 2. Wine and application inputs

| Input | Recorded version or pin | Where supplied |
| --- | --- | --- |
| Wine source | `wine-11.14`, commit `1012f3d99507b80d4869eabf0853567660a7ecbb` | Downloaded from the upstream Wine mirror by the build recipe |
| Upstream input correction | Commit `a1bae27f21b53fa3ac6be6b08f4cbfb441360ade` | Downloaded separately; recipe checks the changed path and resulting Git blob hash |
| Corrected `dlls/win32u/input.c` | Git blob `71219aed5fec18e9c30d5f0d098742814b7248b6` | Checked after applying the correction |
| Forté Agent | 8.00.32.1272, 32-bit application | User-provided installer and license |
| Build platform | Debian 12 amd64; LLVM/Clang 14; Wine dual-architecture WoW64 | User's separate build directory |

The build recipe refuses a different release commit or corrected source hash.
It does not replace system Wine. The exact recipe has not been rebuilt end to end
on a clean Debian installation; successful qualification used the preserved runtime
with this recorded combination. See [the Debian guide](DEBIAN12.md).

# 3. Linux runtime dependencies

These packages are installed through Debian, not included in the archive. Versions
below were observed on the qualification host; they are evidence, not exact package
version requirements. Use distribution security updates and recheck compatibility.

| Package | Observed version | Purpose |
| --- | --- | --- |
| `python3` | 3.11.2-1+b1 | Bridge, setup and launcher; standard-library modules |
| `bubblewrap` | 0.8.0-2+deb12u1 | Filesystem, process and network namespaces |
| `socat` | 1.7.4.4-2 | Optional SMTP/NNTP byte relays |
| `util-linux` | 2.38.1-5+deb12u3 | `findmnt` for selected attachment-folder identity checks |
| `kdialog` | 4:22.12.3-1 | Desktop notifications and terminal-free entry |
| `desktop-file-utils` | 0.26-1 | Desktop-entry validation |
| `python3-gi` | 3.42.2-3+b1 | Optional browser portal's Gio/GLib bindings |
| `gir1.2-glib-2.0` | 1.74.0-3 | Gio/GLib introspection metadata |
| `xdg-dbus-proxy` | 0.1.4-3+deb12u2 | Filtered browser portal access |
| `dbus-daemon` | 1.14.10-1~deb12u1 | D-Bus runtime and synthetic portal test |
| `xdg-desktop-portal` | 1.16.0-2 | Linux browser handoff |
| `xdg-desktop-portal-kde` | 5.27.5-2 | Tested KDE portal backend |

The Python source imports no external Python dependency other than `gi`; there is
no pip install step. Package dependencies bring their own shared libraries and
licenses. Their Debian notices are available under `/usr/share/doc/PACKAGE/copyright`.
Those notices contain component-specific terms; the project's MIT license does not
replace them.

The recipes also use Debian's normal Bash/coreutils tools and the system certificate
store supplied by `ca-certificates` (included in the build package list). Wine supplies
`wine`, `winepath`, `wineserver` and the Windows components used inside the prefix.

The tested desktop is KDE on X11, with a readable X authority file, an active user
session bus/portal, and a default HTTP/HTTPS browser when link support is enabled.
The isolated client uses the supplied `open_url.py` as its `xdg-open` replacement.
An ordinary user must be permitted to create the namespaces Bubblewrap requires.
The portable launcher does not require the project's private systemd test units,
root-owned paths, production mail state, remote access or a background Codex session.
The offline test runner can run without external provider access.

# 4. Build dependencies and private configuration

The explicit Wine build package list is [debian12-packages.txt](../debian/debian12-packages.txt).
It covers compilers, LLVM 14, Git/curl, build tools and the selected X11, font,
TLS and OpenGL development libraries. It is reconstructed from the working host,
not a proven minimal dependency set. Sound, printing and fresh-profile HTML display
are outside the qualified recipe. No .NET package is needed for the tested functions.

Users create their own private POP mappings, retained delivery history, optional
relay routes, desktop settings and independent Agent profile. Those are private runtime
inputs. The release does not include automatic profile migration, account-UID
discovery or native Agent history conversion; those are genuine setup limitations.
No credentials or existing messages are imported automatically. See [setup](../pop-bridge/SETUP.md) and
[desktop configuration](../pop-bridge/DESKTOP.md).
