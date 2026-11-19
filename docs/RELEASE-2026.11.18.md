# 1. Release scope

Source-and-documentation update for Forté Agent 8 on Debian 12/KDE/X11 using the
recorded private Wine build. This is a documented build workflow and experimental
portable integration, not a bundled Windows application or turnkey installer.
See [dependencies and licensing boundaries](DEPENDENCIES.md).

# 2. Verified results

- Fresh installation into an independent prefix, navigation and normal shutdown.
- Selected Linux file access, browser-link handoff and attachment saving.
- One AT&T POP receiving transaction: 208 messages, no server deletions, clean closure.
- One plain-text AT&T SMTP send, independent recipient receipt and clean closure.
- 151 offline tests, including state, no-delete behavior, diagnostics and failure handling.
- Separate synthetic multi-account, namespace and relay checks described in the preparation notes.

The SMTP test used explicit TLS 1.2 and Agent's option to reuse its successful POP
login. An earlier authentication rejection and later successful retry are recorded
without claiming a proven cause. No private messages, identifiers or headers accompany
this release.

# 3. Deferred tests and limitations

Combined live multi-account receiving and live Usenet qualification were deferred
for this release. They are not claimed as passed. Synthetic tests and results from
the original private deployment remain separate evidence.

The exact build recipe has not been rerun on a clean Debian machine. Other Linux
distributions, Wayland, Microsoft OAuth, general outgoing attachments, printing,
sound and all binary-news behavior are not established by these tests.

Start with a fresh Agent installation whose folders are empty. Keep your existing
Agent installation unchanged, and back up its complete data directory: settings,
saved messages, indexes, filters and download-history records. If an existing
bridge is in use, back up its matching configuration and state while both it and
Agent are closed. This does not require a new email address.
See [Start here](START-HERE.md) for definitions and the setup sequence. Migration
and native download-history conversion are not automatic features of this package.

Revision rc2 clarifies instructions, updates historical status references and
corrects the fictitious history-file example. Executable code is unchanged from rc1.

# 4. Privacy and verification

Only the explicitly enumerated source/documentation files are distributed. No
AGENT.INI, accounts, passwords, secure mail keys, registration codes, mail archive,
diagnostic logs, media, proprietary binaries or private test configuration is included.
Fictitious example addresses use reserved example domains. The public repository
URL and author attribution remain intentionally present.

`SHA256SUMS` covers every other file in the package. The accompanying archive checksum
identifies the exact reviewed ZIP. These checksums detect changes; they are not a
substitute for an independently trusted signature.
