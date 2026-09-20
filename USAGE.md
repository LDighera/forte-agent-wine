# 1. Portable POP bridge — experimental user instructions

Experimental source preview, not a production-qualified installer. These steps
describe the intended workflow; review your specific paths and account mapping
before local activation. Begin with a disposable profile and recoverable backups,
not your only working mail archive. No existing profile is modified automatically.

This package supplies POP receiving only. It does not supply SMTP sending, NNTP,
OAuth, an Agent license, or the patched Wine build. It does not reproduce every
feature of Larry's separately configured Mail and News launcher.

# 2. Prerequisites and private layout

Use the ordinary Linux desktop user, not root or sudo. The tested target is this
Debian 12/X11 host with Python 3, Bubblewrap and the working custom Wine build.
This is not a claim of testing on a fresh Debian installation, Linux Mint or
Wayland. See the separately published Wine build guide for the build procedure.

Have Agent installed in a dedicated, user-owned Wine prefix and a recoverable
backup of the profile. Keep its mail data inside that prefix: this launcher does
not mount arbitrary external mail stores or mapped drives. Do not run the same
prefix through another Wine invocation concurrently. The helper does not install
Agent or initialize a prefix. Do not use a prefix containing unrelated Windows
programs for this candidate.

Keep these locations separate (paths below are descriptions, not shell input):

| Location | Contents and handling |
| --- | --- |
| Package directory | Python code and core modules; keep the supplied layout intact. |
| Setup directory | Private config, imported history and account state; never publish. |
| Wine prefix | Agent program, configuration, stored credentials and local messages. |
| Wine build directory | The matching Wine and wineserver executables and libraries. |
| Session directory | A new, empty, current-user-owned 0700 directory for each run. |

Use canonical absolute paths, not symlinks, and do not nest the setup/state or
session directories inside the Wine prefix. Do not select an entire home, disk,
or parent directory as a prefix. Keep the session path short: each account adds
its name and `/b.sock`, and Unix-domain socket path length is limited. The worker
rejects overlong paths; no automatic relocation or permission repair occurs.

# 3. Offline account setup

1. On the destination Linux desktop, as the ordinary desktop user, close Agent
   and any previous bridge normally before preparing delivery-history snapshots.
   Expected: no writer can change those snapshots. Stop if a profile is still in
   use, a window is hung, or the source account is uncertain; do not force-close.
2. On that same host, prepare private copies of each account's newline-separated
   POP UID history, including any old bridge's committed-delivery ledger.
   Expected: complete history for that mailbox, with snapshots owned by this user
   and mode 0600. Stop on missing or ambiguous history. Message DAT/IDX files and
   deferred-message ledgers are not substitutes. Do not alter source mail files.
3. In a terminal on that Linux desktop, as the same user, with the portable package
   directory as the working directory, run the offline setup helper below.
   Expected: prompts for a new destination and account metadata, never a password.
   Stop on any error; retain partial output and do not reuse its destination.

Step 3's copyable command goes in that terminal, in the package directory:

> python3 setup_bridge.py

4. In that helper, supply each actual inbound Agent account UID, stable short name,
   provider POP host, implicit-TLS port, provider login and unique local port.
   Expected: the final summary matches the intended accounts and imported-history
   counts. Stop and cancel if anything differs; an account UID is not a menu index.
   Do not guess the UID. If its mapping is not established, resolve that before
   activation rather than publishing AGENT.INI or searching its credentials.
5. In that helper, confirm creation only after the summary is correct. Expected:
   a new private config/history/state directory and the offline-success message.
   Stop if creation fails. This step does not activate networking or configure
   Agent. A deliberate no-history start can re-download old mail; use it only
   when that is actually intended, never to bypass missing history.

See SETUP.md for exact import restrictions and interruption behavior.

# 4. Mapping the configuration to Agent

The **provider side** uses the real POP endpoint and verified implicit TLS.
The **Agent side** talks only to its isolated loopback listener. These are not
interchangeable settings.

| Setting | Portable configuration | Agent inbound account |
| --- | --- | --- |
| Account identity | `agent_uid`, with verified history for that account | The same actual inbound account, not a new guessed duplicate |
| POP server | `host`: real provider hostname | `127.0.0.1` |
| POP port | `port`: provider implicit-TLS port | Matching account's `listen_port` |
| Login | `username`: exact provider login | Exactly the same login |
| Password | Not stored in bridge config | Provider-supported password/app password, supplied by Agent |
| Transport encryption | Always certificate-verified TLS upstream | Plain POP on isolated loopback; no SSL/STARTTLS to the local listener |
| Authentication | USER/PASS over verified provider TLS | Standard USER/PASS, not APOP or OAuth |
| Server deletion | No upstream DELE implementation | Leave messages on server; disable deletion/expiry options |

For example, the fictitious account `personal` in config.example.json maps to
loopback port 21110; `second` maps to 21111. Those example provider names do not
resolve and the example UIDs are not recommendations for a real profile.

After activation is separately approved, configure the corresponding inbound
accounts through Agent's Tools → Servers and Accounts in the intended profile.
Do not modify outbound/SMTP accounts. Disable automatic polling and automatic
failed-task retries for initial use. Review the account selection before a manual
poll. Stop on an unexpected account, error dialog or missing mapping; do not test
ordinary provider hostnames from inside the isolated Agent session.

The bridge does not read Agent's profile to verify these selections. Credentials
arrive from Agent and are forwarded in memory. They are not written into bridge
configuration, but Agent may itself retain them in its private profile.

# 5. Launch inputs and session behavior

The launcher entrypoint is debian_launcher.py with role `session`. It requires a
config path and a fresh private session directory. For Agent it also needs the
existing Wine prefix, matching Wine build root, local X11 display and Xauthority
file, followed by the exact installed Windows executable path. Application
arguments are separate argv elements after `--`, not evaluated as shell text.

This is an input reference, **not a command to reconstruct manually**:

| Argument | Value |
| --- | --- |
| `--config` | The setup helper's private config.json |
| `--run-directory` | Fresh private session directory, empty before this invocation |
| `--prefix` | Dedicated existing Agent Wine prefix |
| `--wine-root` | Build directory containing bin/wine and bin/wineserver |
| `--display` | Current local X11 display, for example :0, not a remote display |
| `--xauthority` | Current desktop user's X11 authorization file |
| Arguments after `--` | Exact Windows path to the installed Agent executable and its required arguments |

Before first activation, put the verified local values in a short per-user launch
script and review it. Do not copy Larry's host-specific launcher or hand-transcribe
a long command. That site-specific invocation is intentionally not supplied here:
the correct prefix, data layout and account mapping depend on your installation.

The `session` role selects all configured accounts; it does not accept `--account`.
It starts the brokers and local listeners before the application. The terminal
must remain open. No poll is initiated by the launcher. Use Agent's ordinary Get
New Email only after its inbound account mappings have been verified.

Current unchanged limits are 10 local connections per account per session,
up to 1,000 selected messages / 256 MiB per account transaction, a 10 MiB per-message
limit, and bounded metadata/time. These are upper bounds, not promised throughput.
Large or deferred messages may remain on the server; a zero result does not prove
that every server message has been downloaded. Hitting a worker limit currently
stops the session's mail service rather than silently restarting it.

# 6. Normal close, errors and recovery

1. During an approved session, on the Linux desktop as the same user, finish the
   current Agent operation before closing its main window normally. Expected:
   Wine and its matching wineserver exit, then the listeners and brokers finish
   and the terminal returns. Stop if a popup or hang prevents normal closure;
   never kill Wine or the launcher to force a successful result.
2. If the terminal reports MAIL STOPPED, SESSION STOP or another failure, stop
   polling in that Agent window. Expected: no further work is requested while
   in-flight work can finish. Close normally only when responsive and no modal
   dialog prevents it. Do not retry, clear a provider-disable marker, reset a
   ledger, or reuse the session directory. Retain evidence for inspection.
3. On an approved stop request, Ctrl+C in that same terminal asks workers to stop
   after current work; it does not close Agent for you. Expected: a stop warning
   and normal-close wait. Stop manual input if Agent is unresponsive; the helper
   does not have an automatic force-close or recovery path.

A process exit of zero is not proof that the user saw a message or that every
account is caught up. Keep per-account evidence and delivery ledgers. Back up the
Agent profile and corresponding bridge state while closed as a consistent pair;
restoring only one side can skip mail or cause duplicates. Unexpected power loss,
disk-full, or uncertain commit requires inspection, not automatic retry.

# 7. Release status and exclusions

Provider-free protocol, state and multi-process tests passed; a disposable-profile
GUI/navigation/normal-close test passed on the actual desktop account. The
portable multi-account combination has not been tested with real credentials or
a real mail store. This draft is not a migration or production activation grant.

The included bridge source and documentation carry the MIT license in LICENSE.
Source-only packaging excludes records, private setup, AGENT.INI, mail, UID ledgers,
credentials, Wine/Agent binaries and stale test-only launch artifacts. Do not
upload your private development or runtime directories wholesale. The license
is not a claim of production qualification. No
clean-OS rebuild, OAuth, SMTP/NNTP or larger-message work is implied.
