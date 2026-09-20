# 1. Forté Agent under Wine — experimental POP bridge source

This source-only preview connects Agent's isolated local POP accounts to
certificate-verified TLS providers. It preserves delivery tracking and has no
upstream message-deletion operation. It is not installed or activated by unpacking.

Start with USAGE.md for prerequisites, account mapping, limits and failure
handling. SETUP.md describes offline configuration. The related Wine build guide
is at https://wb6bbb.com/agent-wine/ . The Debian build instructions are at
https://wb6bbb.com/agent-wine/debian12.html . Wine and Agent are not included.

**Not a turnkey installer.** This is the portable POP component developed from
the working installation, not a copy of its complete Mail and News environment.
It requires an existing Agent installation, the appropriate Wine build, private
account setup and a reviewed local launch command. Start with a disposable profile,
not your only mail archive. The portable package has not been qualified with live
mail or on a fresh Linux installation.

# 2. Scope and evidence

- Receiving POP mail only; no SMTP, NNTP, OAuth or password vault.
- Separate provider brokers and an isolated loopback-only Agent/listener namespace.
- One configured launch session, up to ten local connections per account.
- Existing transaction, state and launcher tests plus a two-account synthetic
  real-process test passed during development. A disposable Agent profile passed
  navigation and normal close under the actual desktop user.
- No claim of clean-Debian, Linux Mint, arbitrary Wine versions, or live portable
  migration qualification. A concrete disjoint directory layout must be reviewed
  before activation; mount ancestry is not comprehensively enforced by code.

Run only the documented top-level helpers. Files in core/ are imported internal
dependencies; their old direct command-line entrypoints are disabled. Dormant
legacy APIs remain for minimal extraction and are not supported activation paths.

# 3. Contents and exclusions

Only allowlisted Python sources, fictitious configuration, and documentation are
included. SHA256SUMS covers every other archive member. There are no real account
settings, passwords, AGENT.INI, messages, delivery ledgers, run records, Wine/Agent
binaries or host-specific launch scripts. Configuration and state generated later
by setup are private and must never be added to this source package.

# 4. License and release status

The included bridge source and documentation are distributed under the MIT
license; see LICENSE. Standard license text:
https://opensource.org/license/mit . Copyright (c) 2026 Larry Dighera.

This is an experimental source publication, not a production-qualified release.
Licensing does not establish live-mail readiness. Reports of reproducible problems
are welcome; include versions and a redacted error summary, never account files,
credentials, delivery identifiers or message content.

The bridge source was developed during Larry Dighera's Codex-assisted Agent/Wine
work. Python, Bubblewrap, Wine and Agent are separate dependencies, not relicensed
or distributed by this archive. This candidate is not affiliated with Forté.
