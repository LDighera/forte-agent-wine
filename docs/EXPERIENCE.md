# Forté Agent 8 under Wine — experience report

Tested setup details for preserving an established Agent installation and a
large mail/news archive on Linux.

**Status: working-host experience, not portable deployment qualification.** This
report describes the combination tested on one Debian host. The repository now
also contains [Debian build/install recipes](DEBIAN12.md) and
[experimental portable POP source](../pop-bridge/USAGE.md). These are separate
stages, not a turnkey reproduction of the complete Mail and News deployment.

Host observations recorded September 19, 2026; packaging references updated
September 20, 2026. Start with the [repository README](../README.md).

**Installation instructions:** see [Agent 8 on Debian 12](DEBIAN12.md) for the
dependency list, build/install recipes and first-run procedure. These instructions
are reconstructed from this working host, not clean-machine verified. A fresh
Agent installation, startup, default-folder navigation and normal exit were
tested here. Portable bridge setup is a separate, experimental step.

## 1. What works

Larry tested the application on the desktop while OpenAI Codex performed the
investigation, build and integration work. The Wine patch described here is
upstream Wine work, not a fix invented by Codex.

| Function | Observed result and scope |
|---|---|
| Existing archive | Opened and used the migrated archive, about 28 GiB at migration; this was not just an empty-profile installation. |
| POP reception | Four accounts working through custom local bridges with retained delivery tracking and server deletion blocked. |
| Larger mail polls | A September 17 session committed 333 messages and closed with acceptance checks passing. |
| Usenet reading | Giganews headers and article bodies retrieved. |
| Usenet posting | A progress article was composed, posted and retrieved afterward. |
| SMTP sending | One AT&T plain-text test message was sent and received successfully. |
| Normal exit | Repeated successful sessions closed normally; this is not an exhaustive compatibility claim. |

Other SMTP providers, attachments/BCC combinations, encrypted NNTP and
binary-news features have not been established by these tests. Text rendering
still has some rough edges. Hotmail OAuth support has not been integrated.

## 2. Tested environment

| Component | Tested configuration |
|---|---|
| Application | Licensed Forté Agent 8.00.32.1272, 32-bit Windows application |
| Host | Debian 12 Bookworm, KDE desktop, X11 session |
| Wine | Private side-by-side Wine 11.14 build with the upstream fix below |
| Architecture | New WoW64; the working launcher uses `WINEARCH=wow64` |
| PE build tools | Debian Clang/LLD/LLVM 14 |
| Storage | Native Linux ext4 for the Wine prefix and message store |
| Runtime identity | Ordinary desktop user, not root |

Linux Mint has not been tested here. Neither has a clean-machine installation
of this guide. These are the known working components, not a claim that this
exact old Wine version is required or that newer versions lack the fix.

## 3. Wine build and input fix

The Wine 11.14 source used here includes upstream commit
`a1bae27f21b53fa3ac6be6b08f4cbfb441360ade`, titled
**“win32u: Don't ignore raw mouse input.”** It changes
`dlls/win32u/input.c` and references Wine bug 59986.

- [Upstream commit and complete diff](https://github.com/wine-mirror/wine/commit/a1bae27f21b53fa3ac6be6b08f4cbfb441360ade)
- [Wine bug 59986](https://bugs.winehq.org/show_bug.cgi?id=59986)

Failing sessions on this host produced very large numbers of unknown-input
diagnostics. Successful interactive sessions followed the corrected build.
That evidence does not prove every earlier retrieval crash had the same cause.

For an exact-version build, obtain the official Wine 11.14 source and apply the
referenced upstream change. Configure in a separate empty build directory and
install into a separate Wine installation directory. Do not overwrite the
distribution Wine. If using newer source, first determine whether it already
contains the fix; do not apply it twice.

These are **configuration references, not commands to paste**:

| Configure setting | Purpose |
|---|---|
| `--enable-archs=i386,x86_64` | The tested dual-architecture build configuration |
| `--with-mingw=clang` | Use Clang for the Windows PE targets |
| `--prefix=<separate installation directory>` | Keep this Wine installation separate from the system version |

The LLVM tools, including `llvm-dlltool`, mattered. An earlier GCC/MinGW build
failed in `oleaut32`'s `VarR4FromUI8`; the Clang build path avoided that failure.
Normal Wine build prerequisites, including development libraries, Bison and
Flex, are still required. This guide does not provide a verified complete
dependency list for another machine or distribution.

Both patched and unpatched builds can report `wine-11.14`. That version string
alone cannot prove the fix is installed. Use the intended Wine executable and
its matching `wineserver`, rather than accidentally mixing installations.

## 4. Preserve the profile before testing

The executable and message database are different parts of the installation.
In the tested prefix they reside under `Program Files (x86)/Agent` and the
Windows user's `AppData/Roaming/Forte/Agent`, respectively. Determine the data
directory actually used by your own installation before copying anything.

The following is a migration checklist, not a command sequence or authorization
to overwrite an existing installation:

1. **On the source Windows installation, as its owner:** close Agent normally
   before making a backup/copy. Keep the original untouched. Stop if Agent
   cannot close cleanly or the backup cannot be verified.
2. **On the destination Linux desktop, as the ordinary user:** create a separate
   Wine prefix using the intended runtime, and install your licensed Agent.
   Expect an independent installation. Stop if it points at your only archive
   or would overwrite an existing prefix. Do not try to change an existing
   prefix's architecture by changing an environment variable.
3. **With Agent closed on both systems:** place an independent writable copy
   of the complete profile in the destination's correct data directory.
   Preserve settings, indexes, filters and mail UID tracking together, not just
   selected `.DAT` files. Verify the copy and the desktop user's access to its
   parent directories. Stop on a mismatch or unexpected existing destination.
   Do not hard-link a writable store to the only backup.
4. **On the Linux desktop:** first open that copy with provider access blocked
   independently of Agent's saved settings. Expect the original folders and
   readable old messages. Stop if the wrong profile appears, a new-account
   wizard unexpectedly opens, or the program hangs. Do not enable networking
   to work around an offline/profile failure.
5. **In the responsive Agent window:** close normally after checking the
   archive. Expect the application to exit. Stop further tests if it hangs;
   preserve the backup and investigate before reopening the working copy.

Do not let Windows and Wine write the same store concurrently. A Wine prefix
is a separate environment, not by itself a network or security sandbox.

The setup carried forward Wine Gecko 2.47.4 x86. No Microsoft .NET requirement
was demonstrated for the tested Agent functions. For reference, the launcher
uses `WINEDLLOVERRIDES=mscoree=d;winemenubuilder.exe=d`, disabling CLR loading
and Wine's automatic menu generation. This is an observed setting, not a claim
that every Agent installation needs these overrides.

## 5. Usenet and outgoing mail

Giganews header/body retrieval, composition and posting worked. The progress
article in `alt.usenet.offline-reader.forte-agent` was posted from Agent under
Wine and retrieved afterward; it was not a local simulation.

The tested news connection used port 119 without encryption, by Larry's choice.
Unencrypted NNTP can expose authentication and article traffic. This is not a
recommendation to disable encryption elsewhere, nor a test of encrypted NNTP.

Because Agent is network-isolated in this deployment, a standard `socat` byte
relay supplies the selected news connection. It is a transport path, not a
replacement NNTP client or OAuth adapter. Its use here does not demonstrate
that an ordinary Wine installation requires a news proxy.

For an initial online test in your own installation, inspect old Outbox items
first and begin with headers and one article body from an existing subscription.
Expect a successful task, possibly with zero new headers. Stop on errors.
Do not use **Send All** as a connectivity test. Before any intentional post,
verify the group, identity, content and absence of unintended email recipients.

The AT&T SMTP test used Agent's existing TLS/login settings through a byte relay
to the selected SMTP service. A successfully delivered plain-text message
qualifies that path, not all email functions or providers.

News posting and emailing a copy are separate operations. The original Usenet
post succeeded while its added email BCC failed. On a partial or ambiguous
result, check whether the article already appeared before retrying; otherwise
a second news post may be created.

## 6. The custom POP component

This is the largest difference between the tested deployment and simply
installing Agent under Wine. Agent talks to local POP listeners; native Linux
helpers connect to real mail providers over certificate-verified TLS.

Each poll inventories the server's UIDL/LIST snapshot, compares retained
delivery tracking and offers untracked messages to Agent. Bodies are fetched
on demand using disk-backed temporary storage. Agent continues applying its
local routing filters. Completed deliveries are recorded across sessions.

Current limits are:

- Up to 1,000 messages and 256 MiB per account per poll.
- Up to ten polls per session.
- A 10 MiB maximum per message.
- Inventory bounds of 100,000 rows and 8 MiB per metadata response.

The old ten-message-per-account limit no longer describes this launcher.
Nevertheless, “all new mail” is still subject to these safeguards. Another
successful poll can continue resource-limited work. Oversized messages remain
on the provider. An error requires investigation, not an automatic retry.
Zero remaining describes the checked snapshot, not an empty server mailbox
or the absence of later arrivals.

Server retention is deliberate: the upstream helper does not implement POP
`DELE`, and the local side rejects deletion requests. Account-level “leave on
server” settings alone were insufficient because some existing filters had
their own deletion overrides. Those overrides were cleared without removing
the routing rules and have not been restored.

A generic TLS tunnel does not supply this inventory, duplicate tracking or
no-delete enforcement. The repository's [portable POP source](../pop-bridge/USAGE.md)
now extracts these components with explicit account configuration and a Debian
launcher. Its synthetic tests passed, but the combined portable package has not
been qualified against live mail. Its worker connection limits and shutdown
behavior are documented separately. SMTP and NNTP relays remain outside that
portable package; do not equate this host report with a complete installer.

## 7. Troubleshooting lessons and remaining limits

| Observation | What the evidence showed here |
|---|---|
| Input/navigation problems with many unknown-input diagnostics | The upstream raw-input fix was relevant to the working Wine combination; not proof of a universal cure. |
| Blank Subject/Author fields during one test | A temporary copy-on-write area had reached its capacity. Increasing capacity restored the fields; no index repair was needed. |
| News connection failed after an earlier successful poll | A test relay had expired. The failed poll made no provider connection. The normal relay now lasts for the Agent session. |
| Indistinct text or an apparently missing glyph | Still unresolved; separate from the temporary missing-field incident. |
| News post succeeded but an email copy failed | Separate delivery operations. Check the published article before retrying. |

Normal use now has persistent disk-backed profile storage, not the limited RAM
overlay used in that test. These explanations apply to the observed incidents,
not every superficially similar symptom on another machine.

The archive's size alone did not explain the earlier problems: the established
archive remains in use. We have not verified every historical message, every
provider, attachment type or binary-news feature.

## 8. Useful information from other users

When describing a problem, include the Agent build, Wine version and patch
status, distribution, desktop/session type, whether the profile is new or
migrated, the failing operation and a short sanitized error excerpt.

Do not upload complete profiles, account configuration files, mailbox inventories,
credential/token files or unreviewed logs. An installer failure, an offline
folder-navigation hang and a provider authentication rejection need different
investigations.

The combined repository contains project documentation, shell recipes and POP
source, but no proprietary Agent installer, Wine binaries, mail archive, account
credentials or private diagnostic logs. This report is not an official Forté,
Wine or OpenAI support product.
