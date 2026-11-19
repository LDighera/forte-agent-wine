# Portable release — qualification record

This is a record of our tests, not a step-by-step installation guide. Begin with
[Start here](START-HERE.md). Publication of the candidate awaits owner approval.

## 1. Included candidate changes

The POP full-mail path supports 30 MiB complete messages, including a roughly
20 MiB attachment after base64 overhead when the whole message fits. Existing
1,000-message/256 MiB transaction limits and 10 transactions/account/session
remain. Oversized mail stays on the provider; no upstream deletion is supplied.

Optional parameterized SMTP/NNTP socat transports, an ordinary-user desktop
frontend, prefix locking, new-only setup, filtered portal browser handoff and
completed-session recovery are included. See ../pop-bridge/DESKTOP.md for exact
boundaries. These do not import private accounts or registration information.

Recovery deliberately refuses interrupted/ambiguous mail sessions; it is not
an automatic port of all working-host recovery policies. No automatic resend,
ledger clearing or force-close has been added.

## 2. Verified without real accounts

The source suite passes **151 tests** in a network-isolated disposable environment.
It covers full-mail limits, no-delete and commit behavior, lifecycle failures,
private state, duplicate launch, pending/completed recovery, new-only setup and
actual filtered D-Bus traffic against a synthetic portal. Delayed success,
cancellation, denied unrelated methods and proxy exit on launcher death pass.

A separate ordinary-user socat test passed between distinct network namespaces.
It preserves opaque binary payloads and closes active forked connections when
either the client or broker relay is stopped. Its provider is loopback-only;
it is not a TLS interoperability, SMTP delivery or NNTP application test.

Focused independent review found and corrected cleanup/lifetime and prefix
permission issues. Final review found no remaining blocker in that reviewed
source scope. Synthetic evidence is not application/provider acceptance.

From the repository root, copy this into a Linux terminal to run synthetic tests:

> python3 pop-bridge/tests/run_offline.py

Requirements include python3-gi, dbus-daemon and xdg-dbus-proxy in addition to the
POP test prerequisites. The runner copies source and a validated fictitious
configuration into temporary private state. It does not read a live profile or
connect to the host session bus. Network-sensitive mail tests have audit guards;
qualification also runs the whole suite with no external network interface.

Selected attachment folders now support explicit read-only or writable access at
unchanged Linux paths. Filesystem identity and protected/hidden-home alias checks
run before launch. Actual namespace probes verify read/save permissions and reject
protected bind aliases/nested mounts; no user media was read. This source and filesystem qualification is distinct from the subsequent
Agent UI test below.

## 3. Fresh installation and empty-profile desktop check

November 16: the packaged MSI helper installed the original licensed Agent MSI
into a new prefix using the existing verified Wine runtime, under an ordinary
user in a network-isolated service. The executable matched the known Agent build.
No old profile, credentials or registration key was imported.

The portable setup and desktop coordinator opened the welcome screen. Selecting
the normal trial option, then cancelling the setup wizard, reached the main
window. The operator confirmed empty Inbox/Drafts and closed normally. Wine,
workers and the coordinator finished successfully; completion was recorded and
the pending marker cleared. A separate welcome-screen cancellation also finalized
cleanly. This qualifies the fresh desktop lifecycle, not real mail or a clean
Debian rebuild.

## 4. Wine filesystem and Linux browser qualification

November 16: the existing isolated test prefix used Wine cmd to read a synthetic
file, copy it into a selected writable folder and reject a write into a selected
read-only folder. The resulting file contents were verified. Wine's browser
handler then used the packaged filtered desktop-portal route to open a localhost
page in the actual Linux browser. HTTP success and operator visual confirmation
both passed. The probe and temporary page server exited; no test process remained.

No Agent.exe, real message, account or provider was used in this check. An initial
trial stopped before Wine because a diagnostic guard mishandled an expected
PermissionError on a hidden production path. That guard was corrected and tested;
only the separately approved corrected trial produced the accepted results.

## 5. Agent message-link and attachment UI qualification

November 16: Agent imported one synthetic local RFC822/MIME message into Inbox.
Double-clicking its HTTP link displayed the expected localhost page in the Linux
browser. Save Attachment As wrote the included text attachment to the selected
writable folder; its bytes matched the original exactly. Agent closed normally,
recorded completed-session evidence and cleared its pending marker. The temporary
page server stopped normally and released its port. No provider was contacted.

This qualifies these specific UI workflows. It does not establish outgoing
attachment composition/delivery, general attachment-format support or live mail.

## 6. First live receiving attempt and diagnostic correction

November 17: a single authorized receiving attempt authenticated successfully,
then failed at POP LIST with a protocol exception after approximately 81 seconds.
No message was selected, fetched or committed; no server deletion was available.
The test session stopped, its failure gates remained, and the working installation's
delivery history was verified unchanged. The old diagnostic cannot distinguish a
negative server reply from an unexpected connection closure.

The staged candidate now records only fixed diagnostic categories, including
connection closure, negative server response, excessive response-line length and
timeout. It does not retain raw server or exception text. The launcher notice uses
the recorded login result and failure phase to explain post-login failures;
Agent's own generic authentication popup may still appear. Diagnostics do not
change POP commands, protocol limits, retry/stop policy, or delivery tracking.

The 151-test network-isolated suite includes synthetic successful-login/LIST
failures, actual password-phase failure, private-text exclusion, malformed notice
fallback, coordinator notification, and preservation of the original transaction
failure if writing its diagnostic fails. The new failure notice has not yet been
observed during an actual provider failure.

A separately approved diagnostic retry then completed one AT&T POP transaction:
208 messages were fetched and committed by both the broker and local listener.
No server deletion command was sent. The test Agent closed normally, both services
stopped without failures, completed-session evidence was written, and the pending
marker cleared. Original-installation delivery history remained unchanged. Before
this retry the two failure gates from the zero-retrieval attempt were preserved
under locks after explicit recovery approval; no failed run was relabeled successful.
This qualifies one receiving account and clean shutdown, not the multi-account
combination, outbound mail, or news. It does not establish the earlier LIST failure's
cause, and no test-only delivery history was merged into the working installation.

## 7. Live SMTP qualification

November 18: one selected plain-text email was accepted for sending and independently
confirmed in the intended mailbox. The operator supplied the received message; its
subject/body matched the test and its headers reported SPF, DKIM and DMARC passing.
Raw headers, addresses and identifiers are excluded from this package. The TLS 1.3
provider-to-provider header is not evidence of TLS 1.3 in Agent's connection.

The previous attempt returned AUTH0055 (too many bad authentication attempts).
After normal closure, the test configuration was changed with approval to reuse the
POP login that had succeeded, and a separate one-message retry followed an overnight
wait. That retry passed with explicit TLS 1.2 and the existing implicit-TLS relay.
The evidence does not isolate credential selection versus provider throttling as
the earlier cause. No password was decoded, published or replaced.

Final closure was inactive/exit0, with no remaining Agent/Wineserver, completed-session
record present and no pending marker. Production delivery history remained unchanged.
This qualifies one provider/plain-text workflow; multi-account POP, NNTP, outgoing
attachments and broader compatibility remain separate qualifications.

## 8. Release decision and deferred scope

Combined live multi-account POP and live NNTP tests are deferred for the November 18
release at the project owner's direction. They have not been run to completion and
are not claimed as passed. No additional provider testing is required to prepare this
source candidate. Those results must be established separately before broader claims.

The dependency inventory and privacy review accompany the release preparation.
The exact archive is checked against its complete file manifest. Final owner approval
of that archive and its GitHub destination is still required before publication.

A fresh private prefix reused the verified Wine build; that does not establish a
clean-Debian rebuild of the published recipe. That limitation stays visible.
No private AGENT.INI, profile, registration key, credentials, mail, installer or
binary runtime may enter the public archive. See [release notes](RELEASE-2026.11.18.md).
