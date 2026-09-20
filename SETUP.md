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

Each existing mailbox needs complete, closed-profile delivery history. Import
one to eight snapshots of newline-separated POP UIDs, including any previous
bridge's committed-delivery ledger. Source files must be user-owned 0600 regular
files, without symlinks or hard links. Missing/malformed/collectively empty history
is refused; total setup input is bounded at 64 MiB. Message DAT/IDX files and
deferred-message ledgers are not delivery-history substitutes.

An explicitly confirmed start without history is available. It may download old
server mail again; it does not mean the mailbox is empty. The helper cannot infer
the correct account UID or the completeness of imported history.

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
