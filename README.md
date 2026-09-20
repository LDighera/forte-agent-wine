# Forté Agent 8 under Wine: guide, Debian recipes and POP bridge

Keep an established Agent installation useful on Linux. This repository brings
together the experience report and Debian build/install recipes originally
published at [wb6bbb.com/agent-wine](https://wb6bbb.com/agent-wine/), plus the
experimental portable POP bridge source. You no longer need a separate website
download to obtain the project guide, recipes or bridge code.

**Start with basic Agent operation, not your only mail archive.** This is a
documented working configuration and experimental source, not a turnkey installer.
You supply your own licensed Agent installer. Wine source and Debian packages are
downloaded separately by the build workflow; no proprietary software, accounts,
credentials or messages are included here.

## 1. Choose your starting point

| Your goal | Read or use |
| --- | --- |
| Understand what worked and why | [Experience report](docs/EXPERIENCE.md) or [plain-text guide](docs/agent-wine-guide.txt) |
| Build Wine and install a fresh Agent on Debian 12 | [Debian instructions](docs/DEBIAN12.md), using the files in [debian/](debian/) |
| Preserve an existing message store | [Profile-preservation checklist](docs/EXPERIENCE.md#4-preserve-the-profile-before-testing), after fresh-profile operation works |
| Explore the portable POP receiving bridge | [POP usage and account mapping](pop-bridge/USAGE.md), then [offline setup](pop-bridge/SETUP.md) |
| Understand Usenet or outgoing-mail results | [News and SMTP observations](docs/EXPERIENCE.md#5-usenet-and-outgoing-mail); these relays are not packaged here |

Download the complete repository with GitHub's **Code → Download ZIP**, or clone
it. Keep its directory structure intact. Follow the working-directory instructions
in each guide; do not run scripts simply because you have downloaded them.

## 2. What is included

```text
README.md                 Starting point and qualification limits
LICENSE                   MIT terms for this project's code and documentation
SHA256SUMS                Checksums for the other distributed files
docs/
  EXPERIENCE.md           Working-host results and troubleshooting lessons
  DEBIAN12.md             Dependency, build, install and first-opening procedure
  agent-wine-guide.txt    Plain-text account of the same experience
debian/
  debian12-packages.txt   Reconstructed Wine-build dependency list
  build-wine-debian12.sh  Separate Wine build; does not replace system Wine
  agent-debian12.sh       Basic fresh-prefix install/open helper
pop-bridge/
  USAGE.md, SETUP.md      POP workflow, mapping, private state and limits
  config.example.json    Fictitious settings, never a ready-to-use account
  setup_bridge.py        Interactive offline configuration/history import
  debian_launcher.py     Isolated multi-account POP/Agent session coordinator
  ...                    Worker, state and configuration modules
  core/                  Internal protocol dependencies, not standalone tools
```

No generated configuration, AGENT.INI, private profile, mailbox inventory, delivery
ledger, test record, Agent installer/license key, Wine binary or built executable
is distributed. Keep files generated during setup outside the source repository.

## 3. Suggested sequence

1. **Read the Debian guide.** Its target is Debian 12 amd64 with KDE/X11, Agent
   8.00.32.1272 and a separate Wine 11.14 WoW64 build containing the documented
   upstream raw-input fix. This records a tested combination; it does not claim
   that newer Wine versions need the same patch.
2. **Build and check a fresh installation.** Use your own Agent MSI and an empty
   prefix. Test navigation and normal close without entering real account details.
   The basic helper does not isolate networking; Agent's Offline status is not
   a firewall. The guide states what must be checked and when to stop.
3. **Preserve the archive before migration.** Work on an independent copy of the
   complete, closed profile. Keep a recoverable backup and arrange network
   isolation before opening imported accounts. Never share one writable store
   between Windows and Wine.
4. **Treat POP integration as a separate experiment.** Review the bridge's account
   mapping and disjoint directory layout. Import complete existing delivery
   history before activating an established mailbox. A site-specific launch
   invocation still needs to be prepared; the repository does not automatically
   discover your profile, account UIDs or credentials.

## 4. Two launchers, different purposes

| | Basic Agent helper | Portable POP session |
| --- | --- | --- |
| Entry point | `debian/agent-debian12.sh` | `pop-bridge/debian_launcher.py` |
| Purpose | Install/open a fresh Wine prefix | Run configured POP brokers and isolated Agent/listeners |
| Network isolation | **None supplied** | Separate provider brokers; Agent gets loopback-only networking |
| Server no-delete enforcement | **None supplied** | Upstream DELE absent; local deletion requests refused |
| Account setup | No account import | Offline helper plus explicit Agent account mapping |
| SMTP/NNTP | Not configured by the helper | Not supplied by this portable package |

Do not assume the basic helper inherits the bridge's no-delete or isolation
protections. Do not invoke legacy command-line interfaces under `pop-bridge/core/`.

## 5. What has actually been verified

On the original Debian host, Agent used an established roughly 28 GiB archive;
four-account POP retrieval, Usenet reading/posting, an AT&T plain-text SMTP test
and repeated normal closes succeeded. Details and unresolved display issues are
in the experience report. Those results apply to the host's configured deployment,
not automatically to every component in this portable repository.

For the portable bridge, synthetic tests covered transactions, state, duplicate
tracking, no-delete behavior and launcher failures. A real two-account,
separate-process test delivered synthetic mail without contacting providers.
Agent GUI navigation and normal Wine/server exit were tested separately with a
disposable profile under the actual desktop user.

**Not established:** an end-to-end clean-Debian build using these exact wrapper
scripts; Linux Mint or Wayland compatibility; live-mail qualification of the
portable multi-account combination; SMTP/NNTP relay packaging; Hotmail/OAuth;
complete attachment, printing, sound or binary-news compatibility. The build
recipes were reconstructed and checked, not rerun as a clean-machine rebuild.

## 6. POP behavior and current limits

Agent connects to a separate local POP listener for each configured account.
Native Linux brokers use certificate-verified TLS to the real provider and compare
the mailbox inventory with retained delivery history. Messages are offered on
demand and committed delivery tracking survives sessions. Agent retains its
ordinary local routing filters. Upstream messages are never deleted by the bridge.

Limits include 1,000 selected messages and 256 MiB per account transaction, 10 MiB
per message, and ten local connections per account per session. Oversized or
resource-deferred mail can remain on the server. Connection-limit exhaustion can
stop the portable session's mail service while Agent is still open. On an error,
stop polling; do not clear ledgers or retry blindly. See the POP guide for the
full limits, history requirements, normal-close behavior and recovery boundaries.

## 7. Feedback, credit and license

Use GitHub issues for reproducible reports. Include distribution/session type,
Agent and Wine versions, whether the profile is new or migrated, and the precise
step/error. Never upload account files, credentials, license keys, message content,
delivery identifiers or unreviewed logs.

Larry Dighera exercised Agent and checked its desktop behavior. OpenAI Codex
performed the investigation, coding, build and integration work. The Wine input
correction is upstream Wine work, not a fix invented by Codex.

This project's source and documentation use the [MIT license](LICENSE).
Python, Bubblewrap, Wine and Forté Agent are separate dependencies with their own
terms; their binaries are not included or relicensed. This is not an official
Forté, Wine or OpenAI support product.
