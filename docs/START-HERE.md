# 1. What you are installing

This package supplies source code and instructions for running Forté Agent 8 on
Debian 12 with KDE/X11. It does not contain Agent, Wine binaries, your registration
code, or any mail account. You provide your Agent installer/license, build the
recorded Wine version, and enter your own account settings privately.

Start by installing a **fresh test copy with empty folders**. Your existing Agent
installation and saved messages stay where they are. This does not require a new
email address or a new account with your mail provider.

The basic installation can be followed using [DEBIAN12.md](DEBIAN12.md).
Receiving mail through the optional bridge requires additional preparation.
**Automatic migration of an existing mail archive and conversion of Agent's
native download-history files are not included.** If you cannot identify the
correct account or prepare its history, stop before connecting that account.
The instructions below identify that boundary rather than treating an empty
history file as a safe replacement.

# 2. Terms used in these instructions

| Term | Meaning here |
| --- | --- |
| Wine installation | Linux executables and libraries that run Agent. This is separate from Agent's mail data. |
| Wine prefix | A directory containing Wine's Windows environment, including `drive_c`, registry files and this Agent installation. It is not a security boundary by itself. |
| Agent profile / local message store | Agent's complete data directory: settings, saved mail/news, folders, indexes, filters and native download records. Copying only `agent.exe` or `AGENT.INI` does not preserve it. |
| Fresh profile | New, empty Agent data created by the first launch; no old messages or accounts have been imported. |
| Independent copy | A separate writable copy, with no shared writable files or links back to the original data. Keep the untouched original and a separate backup. |
| Provider mailbox | Messages held by your email service. This is different from the messages saved locally in Agent. |
| Account UID | Agent's internal number for an inbound account. It is not your email address, Linux user ID, or the account's position in a menu. |
| POP UID / UIDL | A provider's identifier for one message. It is not the email's `Message-ID` header and is not the account UID. |
| Download history / delivery ledger | Records of which provider message identifiers were already delivered. The bridge uses these to avoid offering the same messages again. These records do not contain the messages themselves. |
| Bridge | Linux helpers that receive Agent's local POP requests and contact the real mail provider using TLS. |
| Relay | A transport for SMTP or news. Agent still handles authentication and, when selected, TLS. |
| Private | Kept locally under your Linux user account; never included in a GitHub upload or an unreviewed support attachment. |

# 3. Install and check the empty copy

1. As your ordinary Linux desktop user, extract the package into a permanent
   directory. Keep its subdirectories together. Later menu shortcuts point to
   this location. Use `sudo` only for the Debian package-install commands that
   explicitly require it; never run Agent or the Python helpers as root.
2. Follow [DEBIAN12.md sections 2–5](DEBIAN12.md#2-before-starting) to install
   prerequisites, build separate Wine, and install your own Agent MSI. Each
   command specifies its working directory and expected result. Stop on errors.
3. Follow [the first-opening check](DEBIAN12.md#6-first-opening-no-accounts-or-old-archive).
   Cancel the account wizard, check the empty Inbox/Drafts, then close Agent.
   Do not enter mail credentials, retrieve messages or send during this check.
4. Continue to section 4 here only after the empty copy opens and closes normally.
   A successful empty-copy test does not establish that migration or online mail
   will work. The exact build recipe has not been tested on a clean Debian host.

If you use the Debian guide's suggested build location, these are **path
references, not commands to paste**. In the file manager, use your actual home
path in place of `$HOME`; Python setup prompts do not expand `$HOME` or `~`.

| Setup prompt | Path resulting from the Debian recipe |
| --- | --- |
| Agent Wine prefix | `$HOME/agent-wine-debian12/agent-prefix` |
| Wine installation containing `bin/wine` | `$HOME/agent-wine-debian12/wine-install` |
| Agent data, in the tested fresh layout | `agent-prefix/drive_c/users/<Windows-user>/AppData/Roaming/Forte/Agent` beneath the build directory; verify your actual location |

# 4. Choose how to handle existing messages

**Empty-copy test:** keep the new Agent folders empty and keep using your original
installation for your mail. No archive migration is needed for an offline test.

**Use an existing provider mailbox in the empty copy:** decide whether you want
old server messages downloaded into it. A fresh Agent profile does not mean the
provider mailbox is empty. Importing prior download history suppresses those old
messages, but does not copy them into the fresh profile. Starting without history
can download them again. Make that choice deliberately; the bridge cannot infer it.

**Migrate your saved archive:** use the [profile-preservation checklist](EXPERIENCE.md#4-preserve-the-profile-before-testing).
This is an assisted, installation-specific operation, not an automatic step in
this release. Identify the actual source data location and destination first.
The package does not supply a generic Windows-to-Wine profile-copy or native
history-conversion tool. Do not merge files into an existing populated destination
or rename a native history file and assume it is a supported text snapshot.

Before migration or an online test:

1. Close the source Agent and the test Agent normally. Finish any active mail
   operation. If closure fails, resolve that failure before making a working copy.
2. Back up the complete source Agent data directory. For an existing Wine
   deployment, also preserve the entire closed Wine prefix. If a bridge was used,
   preserve its configuration, history, state and desktop settings/run records
   from the same closed session. Keep these backups private and separate from
   the writable test copy; do not hard-link them to it.
3. Use your backup tool's verification, or compare the copied files with their
   source. Check that messages, settings, indexes and history are all included.
   A directory existing at the backup destination alone is not verification.
4. Preserve the original. Do not run two clients against the same writable data.
   Do not alternate retrieval between old and new deployments expecting their
   separate download histories to synchronize; this package does not merge them.

A backup is for recovery, not a command to restore immediately. Restoring only
old history can cause duplicates; restoring newer history without matching saved
messages can suppress messages absent from the restored archive. After an uncertain
mail failure, assess the matching data and history before resuming retrieval.

# 5. Prepare optional mail and desktop integration

Follow these guides in order. All paths entered at Python prompts must be actual
absolute Linux paths, with spaces entered literally and without surrounding quotes.
Shell command examples quote paths for the shell; those quotes are not prompt input.

1. [POP usage](../pop-bridge/USAGE.md): identify each inbound account, decide its
   history policy, and prepare supported history snapshots where required. Account
   creation/identification and native-history conversion need installation-specific
   verification; the helpers do not discover them. Do not use guessed account UIDs.
2. [Offline setup](../pop-bridge/SETUP.md): create the private POP configuration.
   This writes local files only; it does not configure Agent or retrieve mail.
3. [Desktop setup](../pop-bridge/DESKTOP.md): install runtime dependencies, prepare
   optional SMTP/news routes and selected attachment folders, then create and
   check the desktop settings. Configure Agent's matching accounts with scheduled
   retrieval disabled before the first online test. For imported accounts, arrange
   independent network isolation during configuration; the basic launcher does
   not provide it. No generic disconnected account-configuration launcher is
   supplied in this release.
4. Launch the prepared **Forté Agent** menu entry only when you intend to enable
   those routes. The launcher does not request mail itself, but Agent's existing
   automatic retrieval/send settings can initiate traffic. Check them first.
5. Test one chosen account/operation at a time. Verify the receiving folder and
   result before another retrieval; verify independent recipient receipt after
   a send. Stop on an error or ambiguous result instead of repeating the action.
   Do not use Send All or Test Connection as a harmless offline check.
6. Close Agent normally and allow its helpers to finish. See
   [recovery instructions](../pop-bridge/DESKTOP.md#6-recovery-and-diagnostics)
   if a warning remains. An incomplete session has no automatic repair button.

To stop using the test setup, close it normally and leave its directories intact
while you decide what to retain. No system Wine was replaced by the recipe.
Before returning to the old mail setup, account for any messages received or sent
in the test copy; these are not automatically copied back. Do not erase the test
profile or its history as an attempted fix.

# 6. What the release does and does not establish

One AT&T receiving session and one plain-text SMTP send with confirmed receipt
passed. Combined live multi-account receiving and live news tests were deferred,
not passed by assumption. See [release notes](RELEASE-2026.11.18.md) for the full
scope, and [PREPARATION.md](PREPARATION.md) for supporting test history.

Report the numbered step and a short error description when requesting help.
Do not attach `AGENT.INI`, registration details, passwords, saved messages or raw
account/history files. Review logs privately before sharing any excerpt.
