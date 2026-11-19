# 1. Scope and prerequisites

This portable desktop candidate coordinates the POP bridge, optional SMTP/NNTP
TCP relays and Agent in separate namespaces. It does not alter the established
installation. **Fresh empty-profile startup/navigation/closure has passed. Wine file access and Linux browser handoff have also passed. Actual Agent synthetic-message import, browser-link opening and attachment saving
have passed. One AT&T POP receiving transaction (208 messages) and clean shutdown
have also passed. One plain-text AT&T SMTP send, independent receipt and clean
shutdown passed November 18. Combined live multi-account POP and NNTP qualification
are deferred for this release; do not use your only archive to qualify them.**

Start with [the plain-language setup sequence](../docs/START-HERE.md).
Target: Debian 12 amd64, ordinary user, KDE/X11, private Wine build and licensed
Agent 8 installation described in [the Debian guide](../docs/DEBIAN12.md). Additional distribution
packages: python3, python3-gi, gir1.2-glib-2.0, bubblewrap, socat,
xdg-dbus-proxy, dbus-daemon, xdg-desktop-portal, xdg-desktop-portal-kde,
kdialog, desktop-file-utils and util-linux (findmnt). Install dependencies through your normal Debian
package manager. An active desktop portal and default HTTP/HTTPS browser are
required for links. Do not run Wine or these helpers as root/sudo.

# 2. Private account and relay preparation

Close Agent normally. Preserve the existing installation and back up its saved
messages, settings, indexes and download records, plus any matching bridge
configuration/state, as explained in [Start here](../docs/START-HERE.md#4-choose-how-to-handle-existing-messages).
Use a separate fresh test copy or a verified independent copy of the full data.
Follow [USAGE.md](USAGE.md) and [SETUP.md](SETUP.md) to prepare the POP configuration
and supported history snapshots. The helpers do not migrate native Agent history. No passwords, registration code or AGENT.INI belong in this repository.

For SMTP/news, copy services.example.json OUTSIDE the source tree, into your
private configuration directory. Set the copy's permissions to owner-only 0600
using your file manager. Replace example hostnames with your providers' real
names, provider ports and distinct local ports above 1023. The loader rejects
conflicts with POP listeners and duplicate route names/ports. Remove example
routes you will not use from the copy's `routes` array; preserve valid JSON with
no trailing commas. For POP-only sessions, create no routes file and leave the
relay-file prompt blank. No relay stores passwords. The supplied `.invalid`
provider names are deliberate nonworking examples. Neither example file should
be used unchanged to connect to a real account.

Configure Agent's outgoing/news server to the **real provider hostname** and the
corresponding **local listen_port**. The launcher supplies a private hostname
mapping inside Agent's namespace. The outer relay resolves the provider normally.
This preserves the original hostname for TLS certificate checks. Do not disable
certificate verification or replace the hostname with localhost for these routes.

The `security` field records the required Agent setting; socat passes bytes
unchanged and does not enforce or terminate TLS. Match Agent's implicit TLS or
STARTTLS setting to the provider. SMTP plaintext is rejected by configuration;
NNTP plaintext requires explicit `plain` selection and carries no transport
confidentiality. A listening relay is not proof of delivery. Confirm sending
with a controlled recipient; never automatically resend after an ambiguous error.

On the tested Wine 11.14 build, a synthetic Schannel probe supports explicitly
requested TLS 1.2; TLS 1.3 was not supported. `AllowedSSLProtocols=2048` in Agent's
private [Online] section is the TLS-1.2-only candidate for qualification. Close
Agent and preserve its private configuration before editing it. Values 10880
and 2688 also permit older protocols; they are not TLS-1.2-only masks. This does
not solve Microsoft OAuth, and the portable package does not supply a Microsoft
saved-sign-in/token service.

Before an SMTP test, select the intended outgoing server and persona in Agent.
Check the From address, recipient and authentication settings. The successful
AT&T test reused the existing POP account's login through Agent's **Use POP login**
option; this is appropriate only when both services use that same credential.
It is not a universal provider requirement. If using a separate SMTP login,
enter the provider-supported credentials privately. Do not interpret relay
startup as proof that a password is correct. On authentication rejection, stop;
repeated Test Connection/Send attempts can trigger provider throttling.

# 3. Prepare desktop settings without activating anything

As the ordinary desktop user, in the extracted repository root, copy this
command into a Linux terminal:

> python3 pop-bridge/setup_desktop.py

Answer the prompts with actual absolute Linux paths, without quotes. Unlike a
shell, this helper does not expand `~` or `$HOME`. Use the following reference:

| Prompt | Answer |
| --- | --- |
| Existing private Agent Wine prefix | The dedicated `agent-prefix` directory created by the Debian recipe. |
| Private Wine installation | That recipe's `wine-install` directory, which contains `bin/wine`; not the source/build directory. |
| Prepared POP config.json | The actual `config.json` created by `setup_bridge.py`. |
| Private relay routes JSON | Your edited private routes file, or press Enter for POP only. |
| Enable HTTP(S) links | Type `yes` or `no`; `yes` requires the desktop portal dependencies above. |
| NEW private desktop-settings directory | A new short absolute path under your home, such as `/home/your-user/.agent-desktop`, using your real username. |
| Number of attachment folders | 0–16; 0 exposes no additional host folders. |
| Attachment folder and mode | Existing absolute local/mounted path, followed by `ro` or `rw` as described below. |

Choose locations that will remain in place; source files and settings are used by
the launcher after setup.
Long socket paths are rejected. Select existing attachment folders by their actual
Linux paths, including already-mounted NAS/USB folders. Choose `ro` to attach/read
files only, or `rw` to permit saving and overwriting files in that folder. No
special media-folder layout is required. The helper validates first, then asks
for CREATE.
It creates only desktop.json and runs/. No account or application settings are
changed, and no process, provider or launcher is started. Preserve partial output
if creation fails; do not reuse it automatically.

The prefix must be owned by you and not group/world writable. Settings and run
records remain private. Moving the prefix, account state or settings after setup
requires revalidation; absolute identity bindings are deliberate.

# 4. Validate and create the terminal-free entry

The following commands assume you chose `$HOME/.agent-desktop` above. If you chose
another directory, use its actual absolute path. Keep the extracted source tree
in a permanent location: the desktop entry references it.

First, copy this command into the same Linux terminal. It checks settings only:

> python3 pop-bridge/desktop.py check --settings "$HOME/.agent-desktop/desktop.json"

Expected: settings validated, nothing launched. Stop on an error. Then, to create
a NEW application-menu entry, copy this command into that terminal:

> python3 pop-bridge/desktop.py create-entry --settings "$HOME/.agent-desktop/desktop.json" --output "$HOME/.local/share/applications/forte-agent-portable.desktop"

The applications directory must already exist; an existing entry is refused.
If the applications directory is absent, create `.local/share/applications` under
your home using Dolphin before this command. Do not replace an existing entry.
Expected: the command returns without an error and the new `.desktop` file exists;
create-entry does not print a success banner. The menu label is **Forté Agent**.
If you have another entry with that label, inspect its launcher properties and
choose the one whose command references this package's `desktop.py` and your new
`desktop.json`. Do not accidentally open the original mail setup.

Launch that entry only when the test copy, history and account mappings are ready
for the specific validation you intend. No credentials are requested by the
launcher; enter your own credentials privately in Agent when appropriate.

The prefix lock prevents duplicate launches through this desktop frontend.
Direct Wine/basic-helper/debian_launcher invocations bypass that lock: do not mix
launch methods or open the same prefix concurrently. Agent's own scheduled
retrieval settings still apply; disable them in a test copy before qualification.

# 5. Links, files and normal close

Wine's xdg-open path is replaced inside this session by an HTTP(S)-validating
portal helper. The filtered bus exposes OpenURI and its request completion/close
operations, not the unfiltered desktop bus. Scheme validation belongs to the
helper; the proxy filters methods, not URI arguments. A successful portal response
means handoff, not proof that a web page loaded. On failure, copy the link into
your browser. Unsupported schemes/local files are not opened by this helper.

The selected attachment folders are exposed only to Agent's client sandbox, at
their existing Linux paths. In Wine's file chooser, Z: represents the exposed
Linux root: for example, /home/your-user/Downloads is reached through
Z:\home\your-user\Downloads. The package does not yet replace Agent's file chooser
with a native Linux dialog. Files outside the selected folders/prefix are not
made available merely because Z: is present.

Settings version 2 records each folder's filesystem identity. Before each launch,
a missing or changed mount stops startup instead of using a mountpoint's fallback
local directory. Setup does not mount NAS/USB devices. Remount the intended device
through your normal Linux desktop first. Entire homes, hidden home settings,
protected profile/state/runtime paths and their detected bind aliases are rejected.
Folders containing nested mounts are rejected; select the needed mounted folder
itself. Read-only/writable bind behavior and synthetic Wine read/save checks have passed;
Agent also saved an imported synthetic attachment with exact byte agreement.
Outgoing attachment composition and delivery are not established by this test. Select trusted content: this
is not a claim to defend against a malicious process under the same Unix user.

Existing version-1 settings remain readable with no host attachment folders.
Adding access requires an explicit new private setup; there is no silent upgrade. Agent's isolated network has
loopback listeners only; external services are reached by the specified brokers
and routes. This is application isolation, not a hostile-code security claim:
the X11 connection itself has desktop capabilities.

Close Agent normally. The coordinator waits for its Wine server, mail workers
and relay cleanup before recording success. Never treat the window disappearing
alone as successful finalization. With the suggested settings directory, inspect
its newest `runs/run-*` folder in Dolphin (enable Show Hidden Files if needed).
Normal completion creates `completed.json` there and removes
`.portable-agent.pending` from the prefix. These files confirm launcher cleanup,
not that mail was received or sent. Do not create or delete them yourself.

# 6. Recovery and diagnostics

If a service fails, stop polling/sending and close Agent normally when responsive.
There is no automatic resend or forced close. Retain the new run directory under
your private settings runs/ and the prefix's `.portable-agent.pending` marker.

For a recorded POP failure, the launcher notice now distinguishes successful
provider login followed by an inventory/retrieval error from a failure before
login completed. Agent can show a generic password-rejection popup because the
bridge prepares its mailbox inventory during Agent's login request; consult the
launcher notice before changing credentials. Diagnostic reasons are fixed
categories only. Raw provider text is not displayed or logged. Missing or invalid
diagnostic records fall back to the general stop notice, never to a success.
This change is verified with synthetic tests; its new GUI wording awaits a live
trial. It does not clear a failed session or authorize another poll.

A pending marker can be cleared automatically ONLY when an identical completed
record proves the previous session finished normally under unchanged settings.
Incomplete or failed sessions remain blocked. This portable version does not
automatically reconcile an interrupted mail delivery with Agent's local archive.
Do not delete pending/provider-stop files, reset UID ledgers or repeatedly launch
to bypass the stop. Record the exact popup and operation, then inspect the newest private run folder
under the settings directory. Broker logs and `client-session.log` can help locate
the failure. There is no packaged command to repair an uncertain delivery. Request
help with a sanitized error description if you cannot establish what completed;
do not upload the profile or full run folder. Resolve the cause and assess the
matching saved messages/history before another poll. The broader automatic
recovery of the author's private installation is not included here.

Run records can contain private account identifiers and provider information.
Do not upload them unreviewed. Logs are not automatically pruned because failed
transaction evidence can be needed for recovery. After a verified normal session,
older successful diagnostics can be archived separately from the live profile and
account state. No retention cleanup is performed by this launcher.
