# 1. Offline setup reference

setup_bridge.py is interactive and requires Python 3 and adjacent source modules.
It refuses root/sudo, requests no password, imports no networking runtime, and
does not open sockets, start services, or read/modify AGENT.INI. USAGE.md gives
the intended operator workflow; no activation occurs during setup.

# 2. Inputs

Supply a new absolute destination beneath an existing user-owned directory that
is not group/world writable. Existing destinations are refused. Supply one to
sixteen account mappings: stable name, actual inbound Agent UID, real provider
POP hostname, implicit-TLS port, exact login name, unique loopback port.

To avoid downloading previously received messages again, supply verified
download history for that exact provider mailbox, taken while the source Agent
and any previous bridge were closed. This history is a list of identifiers, not
a backup of saved messages. The helper does not convert native `mailuids*.dat`
files or discover account UIDs. Stop for assistance if those inputs are not known.
Import one to eight snapshots of newline-separated POP UIDs, including any previous
bridge's committed-delivery ledger. Source files must be user-owned 0600 regular
files, without symlinks or hard links. Missing/malformed/collectively empty history
is refused; total setup input is bounded at 64 MiB. Each line must contain only
one exact provider POP identifier (printable ASCII, no spaces), with no headings,
message headers or metadata. A syntactically valid file is not proof of completeness. Message DAT/IDX files and
deferred-message ledgers are not delivery-history substitutes.

For `History mode`, type `import` only when the snapshots above are ready.
Choose `new` only if you intentionally want to start without any download history;
the next confirmation is `START WITHOUT HISTORY`. It may download old server mail
again; it does not mean the provider mailbox is empty. Import mode asks for
`IMPORT COMPLETE HISTORY`. The final write confirmation is `CREATE OFFLINE SETUP`.
These are prompt answers, not shell commands. Cancel with Ctrl+C if uncertain.
The helper cannot infer
the correct account UID or the completeness of imported history.

The helper's prompts mean:

| Prompt | What to supply |
| --- | --- |
| New setup directory | Actual absolute Linux path to a directory that does not yet exist; choose a permanent location outside the prefix/source tree. Enter no quotes, `$HOME` or `~`. |
| Number of accounts | 1–16; start with one for your first receiving test. |
| Short stable name | Start with a lowercase letter; use only lowercase letters, digits, `_` or `-`, up to 32 characters. This is a local label, not your login. |
| Agent account UID | Verified internal number of the corresponding inbound Agent account; do not use its menu position or an example number. |
| Provider POP hostname | Real inbound server hostname from your provider's configuration; no `https://` prefix. |
| Implicit TLS port | Provider's implicit-TLS POP port; Enter selects 995. STARTTLS-only servers are not supported by this bridge. |
| Provider login name | Exact provider-supported login, often the full email address; no password. |
| Unique local port | A distinct port from 1024 to 65535 for each account; 21110 is an example, not a provider port. Enter the same number in Agent later. |
| History mode and snapshot paths | Follow the import/new distinction above. Paths refer to prepared private snapshots, not live native Agent files. |

For private snapshot files, mode `0600` means owner read/write, nobody else.
For generated directories, `0700` means owner read/write/access, nobody else.
In Dolphin, use Properties → Permissions to restrict access to the owner; verify
actual numeric permissions if the helper rejects a file. Do not change permissions
on original live files just to satisfy the importer; use verified private copies.

# 3. Output and stop behavior

After validation and final confirmation, setup creates private config.json,
history/ snapshots, and separate state/ account bindings, seeds and ledgers.
Directories are 0700; files are 0600. Existing sources are not changed. Identity
bindings include absolute paths: moving a setup is not a supported rename.

Cancellation before creation writes nothing. A filesystem failure during creation
can leave partial output; retain it, do not reuse it or attempt automatic repair.
Successful setup means offline preparation only. It is not live-mail acceptance.

Keep generated setup files and the Agent profile private. Back up matching profile
and bridge state together while closed. Never upload them with this source.
