#!/usr/bin/env python3
"""Persistent, no-delete POP bridge for a nominal Forte Agent session.

The outside-namespace broker makes at most one verified-TLS provider
connection for each inside-namespace Agent POP connection.  The listener keeps
the broker connection open until it can acknowledge which complete RETR
responses were served to Agent.  Only those UIDs are atomically committed to a
protected session ledger.  No credential, UID, or message content is logged.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import importlib.util
import os
import poplib
import re
import signal
import socket
import ssl
import struct
import sys
import time
from pathlib import Path
from types import FrameType, ModuleType

# Scripts are also loaded through importlib by provider-free tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pop_backlog
import pop_catchup_budget


REQUEST_MAGIC = b"FAPN1"
COMMIT_MAGIC = b"FAPC1"
COMMIT_OK = b"FAPO1"
MAX_SAFE_CATEGORY_BYTES = 128
MAX_BATCH_MESSAGES = 50
MAX_SESSION_CONNECTIONS = 10
# Bound repeated head checks independently of the resumable backlog window.
MAX_HEAD_CANDIDATES = 100
# UID1 is the configured ATT inbound account. Backlog windows are unchanged.
MAX_ATT_HEAD_CANDIDATES = 25
# Cooperative limit: finish the current candidate, then RSET/QUIT. Socket
# timeouts still bound individual operations; this is not a hard wall deadline.
SCAN_WORK_SECONDS = 60.0


class SessionBridgeError(RuntimeError):
    """A failure whose category is safe to record."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def provider_stop_path(args):
    return args.evidence_dir.parent / 'PROVIDER-ACCESS-DISABLED'


def provider_disabled(args):
    path = provider_stop_path(args)
    return path.exists() or path.is_symlink()


def disable_provider(args):
    """Close shared admission before returning a failure to Agent; never reopen."""
    path = provider_stop_path(args)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return  # Any existing entry is fail-closed; do not trust/read its text.
    with os.fdopen(fd, 'wb') as stream:
        stream.write(b'provider_admission=disabled\nreason=fatal-transaction\n')
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_base_bridge(path: Path) -> ModuleType:
    if not path.is_file() or path.is_symlink():
        raise SessionBridgeError("base_bridge_invalid")
    spec = importlib.util.spec_from_file_location("nominal_session_base_bridge", path)
    if spec is None or spec.loader is None:
        raise SessionBridgeError("base_bridge_import_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def safe_category(value: str) -> str:
    return re.sub(r"[^a-z0-9_.-]", "_", value.lower())[:MAX_SAFE_CATEGORY_BYTES]


UPSTREAM_PHASES = frozenset(('connect', 'user', 'pass', 'stat', 'uidl', 'list',
                             'retr', 'message', 'rset', 'quit'))
UPSTREAM_ISSUES = {
    'connection_eof': 'connection closed',
    'server_negative_response': 'server returned an error',
    'response_line_too_long': 'response line too long',
    'invalid_response': 'invalid server response',
    'timeout': 'operation timed out',
    'tls_certificate': 'TLS certificate verification failed',
    'tls_eof': 'TLS connection closed',
    'tls_error': 'TLS operation failed',
    'connection_reset': 'connection reset',
    'broken_pipe': 'connection write failed',
    'io_error': 'network operation failed',
    'validation_error': 'local response validation failed',
}


def upstream_issue_kind(exc: Exception) -> str:
    """Classify locally; never return provider text, exception repr, or tokens."""
    if isinstance(exc, poplib.error_proto):
        value = exc.args[0] if len(exc.args) == 1 else None
        # poplib's synthetic EOF/length errors are strings. Real response
        # lines are bytes, even if the server happens to send '-ERR EOF'.
        if type(value) is str and value == '-ERR EOF':
            return 'connection_eof'
        if type(value) is str and value == 'line too long':
            return 'response_line_too_long'
        if type(value) is bytes and (value == b'-ERR' or value.startswith(b'-ERR ')):
            return 'server_negative_response'
        return 'invalid_response'
    if isinstance(exc, ssl.SSLCertVerificationError): return 'tls_certificate'
    if isinstance(exc, ssl.SSLEOFError): return 'tls_eof'
    if isinstance(exc, ssl.SSLError): return 'tls_error'
    if isinstance(exc, TimeoutError): return 'timeout'
    if isinstance(exc, ConnectionResetError): return 'connection_reset'
    if isinstance(exc, BrokenPipeError): return 'broken_pipe'
    if isinstance(exc, OSError): return 'io_error'
    return 'validation_error'


def negative_response_hint(exc: Exception) -> str:
    """Return only fixed hints; never retain or emit arbitrary provider text."""
    if not isinstance(exc, poplib.error_proto) or len(exc.args) != 1:
        return 'not-applicable'
    value = exc.args[0]
    if type(value) is not bytes or len(value) > 4096 or not (value == b'-ERR' or value.startswith(b'-ERR ')):
        return 'not-applicable'
    body = value[4:].strip().lower()
    for tag, hint in ((b'[sys/temp]', 'temporary'), (b'[sys/perm]', 'permanent'),
                      (b'[in-use]', 'mailbox-busy'), (b'[login-delay]', 'rate-or-delay'),
                      (b'[auth]', 'authentication-tag')):
        if body == tag or body.startswith(tag + b' '):
            return hint
    for phrases, hint in (
        ((b'no such message', b'invalid message number', b'message does not exist'), 'message-number'),
        ((b'rate limit', b'too many', b'try again later'), 'rate-or-delay'),
        ((b'mailbox locked', b'maildrop locked'), 'mailbox-busy'),
        ((b'temporary', b'temporarily'), 'temporary'),
    ):
        if any(phrase in body for phrase in phrases):
            return hint
    return 'unspecified'


def diagnostic_number(value) -> int:
    """Numeric mailbox position/count only; malformed values never become text."""
    return value if type(value) is int and 0 <= value < 2**63 else 0


def note_upstream_issue(evidence, phase: str, exc: Exception) -> None:
    # Preserve the original issue if cleanup subsequently fails too.
    if getattr(evidence, 'upstream_issue_phase', 'none') == 'none':
        evidence.upstream_issue_phase = phase if phase in UPSTREAM_PHASES else 'message'
        evidence.upstream_issue_kind = upstream_issue_kind(exc)
        evidence.upstream_response_hint = negative_response_hint(exc)


def upstream_diagnostic(evidence) -> str:
    return '.'.join(('upstream-diagnostic', evidence.upstream_issue_phase,
                     evidence.upstream_issue_kind, evidence.upstream_login_completed))


def bridge_error_reply(exc: Exception) -> bytes:
    # Only exact locally defined enum values may reach Agent's task-error text.
    category = getattr(exc, 'category', None)
    fields = category.split('.') if type(category) is str else []
    if (len(fields) == 4 and fields[0] == 'upstream-diagnostic'
            and fields[1] in UPSTREAM_PHASES and fields[2] in UPSTREAM_ISSUES
            and fields[3] in ('yes', 'no')):
        login = 'provider login succeeded' if fields[3] == 'yes' else 'provider login not completed'
        return (f'-ERR mail bridge: {login}; {fields[1].upper()}: '
                f'{UPSTREAM_ISSUES[fields[2]]}; stop polling').encode('ascii')
    return b'-ERR mail bridge failed safely; this does not establish a password error; stop polling'


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        mode,
    )
    try:
        os.write(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode)


def read_ledger(base: ModuleType, path: Path) -> set[bytes]:
    if not path.is_file() or path.is_symlink():
        raise SessionBridgeError("delivered_ledger_invalid")
    values: set[bytes] = set()
    for value in path.read_bytes().splitlines():
        if not base.valid_uid(value):
            raise SessionBridgeError("delivered_ledger_malformed")
        values.add(value)
    return values


def merge_ledger(base: ModuleType, path: Path, additions: set[bytes]) -> None:
    if any(not base.valid_uid(value) for value in additions):
        raise SessionBridgeError("commit_uid_invalid")
    values = read_ledger(base, path)
    values.update(additions)
    atomic_write_bytes(path, b"".join(value + b"\n" for value in sorted(values)))


def send_blob(stream: socket.socket, value: bytes) -> None:
    stream.sendall(struct.pack("!I", len(value)) + value)


def receive_exact(stream: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = stream.recv(count - len(chunks))
        if not chunk:
            raise SessionBridgeError("protocol_truncated")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_blob(stream: socket.socket, maximum: int) -> bytes:
    length = struct.unpack("!I", receive_exact(stream, 4))[0]
    if length > maximum:
        raise SessionBridgeError("protocol_blob_too_large")
    return receive_exact(stream, length)


def send_commit(stream: socket.socket, uids: set[bytes]) -> None:
    stream.sendall(COMMIT_MAGIC + struct.pack("!I", len(uids)))
    for uid in sorted(uids):
        send_blob(stream, uid)


def receive_commit(
    base: ModuleType,
    stream: socket.socket,
    selected: list[object],
) -> set[bytes]:
    if receive_exact(stream, len(COMMIT_MAGIC)) != COMMIT_MAGIC:
        raise SessionBridgeError("commit_magic_invalid")
    count = struct.unpack("!I", receive_exact(stream, 4))[0]
    if count > len(selected):
        raise SessionBridgeError("commit_count_invalid")
    selected_uids = {message.uid for message in selected}
    committed: set[bytes] = set()
    for _ in range(count):
        uid = receive_blob(stream, base.MAX_UID_BYTES)
        if not base.valid_uid(uid) or uid not in selected_uids or uid in committed:
            raise SessionBridgeError("commit_uid_invalid")
        committed.add(uid)
    return committed


@dataclasses.dataclass
class BrokerAggregate:
    attempts: int = 0
    completed: int = 0
    failed: int = 0
    messages_selected: int = 0
    messages_committed: int = 0
    retrieval_candidates_deferred: int = 0
    retr_error_closed_connections: int = 0
    selected_octets: int = 0

    def text(self, status: str) -> str:
        return "\n".join(
            [
                f"status={status}",
                f"updated_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
                f"connection_attempts={self.attempts}",
                f"transactions_completed={self.completed}",
                f"transactions_failed={self.failed}",
                f"messages_selected={self.messages_selected}",
                f"messages_committed={self.messages_committed}",
                f"retrieval_candidates_deferred={self.retrieval_candidates_deferred}",
                f"retr_error_closed_connections={self.retr_error_closed_connections}",
                f"selected_octets={self.selected_octets}",
                "tls_certificate_verification=system_default",
                "credential_storage=memory-only",
                "uid_values_logged=no",
                "message_content_logged=no",
                "delete_command_available=no",
            ]
        ) + "\n"



class BoundedRetrReader:
    """Relax only RETR body lines; retain the whole-message byte bound.

    Status responses still use the original client's strict _getresp(). Reads
    allow at most the remaining canonical-message bytes plus framing/lookahead.
    No global poplib limit changes and no message rewriting are involved.
    """
    def __init__(self, client, maximum, aborted):
        if type(maximum) is not int or not 0 < maximum <= 10 * 1024**2:
            raise SessionBridgeError('body_limit_invalid')
        self.client = client
        self.remaining = maximum
        self.aborted = aborted

    def __getattr__(self, name):
        return getattr(self.client, name)

    def fail(self, category):
        try:
            self.client.close()
        finally:
            raise self.aborted(category)

    def _getline(self):
        wire = self.client.file.readline(self.remaining + 4)
        if not wire:
            raise poplib.error_proto('-ERR EOF')
        if not wire.endswith(b'\n'):
            self.fail('upstream_body_line_unterminated_or_exceeded_bound')
        octets = len(wire)
        # Preserve the normal poplib CRLF/LF normalization.
        line = wire[:-2] if wire.endswith(b'\r\n') else wire[1:-1] if wire.startswith(b'\r') else wire[:-1]
        if line != b'.':
            canonical_size = len(line) - int(line.startswith(b'..')) + 2
            if canonical_size > self.remaining:
                self.fail('upstream_message_exceeded_bound')
            self.remaining -= canonical_size
        return line, octets


def catchup_scan_state(state, enabled):
    """Catch-up advances the saved backlog every poll; ordinary mode is unchanged."""
    if state is None:
        return None
    pop_backlog.validate(state)
    return dict(state, turn='backlog') if enabled else state


def fetch_batch_for_session(
    base: ModuleType,
    account: object,
    username: bytes,
    password: bytes,
    known_uidls: set[bytes],
    *,
    batch_size: int,
    scan_limit: int,
    max_message_bytes: int,
    timeout_seconds: float,
    pop_factory=None,
    evidence=None,
    scan_state=None,
    reservation=None,
):
    """Fetch a batch with explicit RETR deferral and partial UIDL-EOF handling.

    A deferred UID is never returned, logged, or committed as delivered.  A
    provider RETR refusal can leave that POP connection unusable, so scanning
    stops immediately and the provider socket is closed locally without RSET
    or QUIT.  No DELE command has been sent, so the close cannot commit a
    deletion.  The UID is returned only as protected session state so a later
    Agent poll in the same session can skip it and continue with older
    candidates. An exact POP EOF at the next UIDL may return already complete
    messages, without deferring or consuming that unknown candidate. The next
    normal bounded poll revisits it; there is no reconnect here. Zero-progress
    UIDL EOF and all other protocol, transport, size, and validation errors
    remain transaction failures.
    """
    required = (
        "MAX_CREDENTIAL_BYTES",
        "BridgeConnectionAborted",
        "FetchEvidence",
        "Message",
        "parse_uidl_response",
        "parse_list_response",
        "retr_bounded",
        "canonical_message",
    )
    if not all(hasattr(base, name) for name in required):
        if reservation is not None:
            raise SessionBridgeError('incremental_budget_requires_bounded_fetch')
        selected, evidence = base.fetch_batch(
            account,
            username,
            password,
            known_uidls,
            batch_size=batch_size,
            scan_limit=scan_limit,
            max_message_bytes=max_message_bytes,
            timeout_seconds=timeout_seconds,
            evidence=evidence,
        )
        evidence.provider_cleanup = "rset-quit"
        return selected, evidence, set()
    if username != account.username:
        raise base.BridgeError("agent_username_mismatch")
    if not 1 <= len(password) <= base.MAX_CREDENTIAL_BYTES:
        raise base.BridgeError("agent_password_length_invalid")
    if any(byte < 0x20 or byte > 0x7E for byte in password):
        raise base.BridgeError("agent_password_not_ascii")

    if evidence is None:
        evidence = base.FetchEvidence()
    evidence.upstream_issue_phase = 'none'
    evidence.upstream_issue_kind = 'none'
    evidence.upstream_login_completed = 'no'
    evidence.upstream_response_hint = 'none'
    evidence.upstream_stat_messages = 0
    evidence.upstream_scan_start_number = 0
    evidence.upstream_last_uidl_number = 0
    phase = 'connect'
    client = None
    authenticated = False
    selected: list[object] = []
    failure = None
    connection_usable = True
    deferred = 0
    deferred_uids: set[bytes] = set()
    provider_cleanup = "rset-quit"
    scan_started = time.monotonic()
    scan_stop = "window-complete"
    try:
        context = ssl.create_default_context()
        factory = poplib.POP3_SSL if pop_factory is None else pop_factory
        client = factory(
            account.host,
            account.port,
            timeout=timeout_seconds,
            context=context,
        )
        evidence.commands.append("USER")
        phase = 'user'
        client.user(username.decode("ascii"))
        evidence.commands.append("PASS")
        phase = 'pass'
        client.pass_(password.decode("ascii"))
        authenticated = True
        evidence.upstream_login_completed = 'yes'
        evidence.commands.append("STAT")
        phase = 'stat'
        count, octets = client.stat()
        evidence.upstream_stat_messages = diagnostic_number(count)
        evidence.server_message_count = count
        evidence.server_message_octets = octets

        start_number = count if scan_state is None else pop_backlog.start(scan_state, count)
        evidence.upstream_scan_start_number = diagnostic_number(start_number)
        candidate_limit = scan_limit
        if scan_state is not None and scan_state['turn'] == 'head':
            head_limit = MAX_ATT_HEAD_CANDIDATES if account.uid == 1 else MAX_HEAD_CANDIDATES
            candidate_limit = min(candidate_limit, head_limit)
        first_number = max(1, start_number - candidate_limit + 1)
        next_number = start_number
        for number in range(start_number, first_number - 1, -1):
            if len(selected) >= batch_size:
                scan_stop = "batch-full"
                break
            if time.monotonic() - scan_started >= SCAN_WORK_SECONDS:
                scan_stop = "work-budget"
                break
            evidence.upstream_last_uidl_number = diagnostic_number(number)
            evidence.commands.append("UIDL")
            phase = 'uidl'
            try:
                response = client.uidl(number)
            except poplib.error_proto as exc:
                if not (authenticated and selected and upstream_issue_kind(exc) == 'connection_eof'):
                    raise
                note_upstream_issue(evidence, phase, exc)
                connection_usable = False
                provider_cleanup = "closed-after-uidl-eof"
                scan_stop = "uidl-eof-partial"
                next_number = number  # No UID was received; revisit this candidate.
                break
            uid = base.parse_uidl_response(response, number)
            evidence.candidates_scanned += 1
            next_number = number - 1
            if uid in known_uidls or any(message.uid == uid for message in selected):
                evidence.known_candidates_skipped += 1
                continue
            evidence.commands.append("LIST")
            phase = 'list'
            listed_size = base.parse_list_response(client.list(number), number)
            if listed_size > max_message_bytes:
                evidence.oversized_candidates_skipped += 1
                continue
            if reservation is not None and not reservation.claim():
                # No RETR was sent. Revisit this exact unconsumed candidate next time.
                next_number = number
                scan_stop = 'byte-budget'
                break
            evidence.commands.append("RETR")
            phase = 'retr'
            try:
                _response, lines, _reported_octets = base.retr_bounded(
                    BoundedRetrReader(client, max_message_bytes, base.BridgeConnectionAborted),
                    number, max_message_bytes
                )
            except poplib.error_proto as exc:
                note_upstream_issue(evidence, phase, exc)
                deferred += 1
                deferred_uids.add(uid)
                # A live provider can close or poison its POP transaction after
                # rejecting RETR.  No DELE has been sent, so close locally and
                # let the next Agent poll skip this session-deferred UID.
                connection_usable = False
                provider_cleanup = "closed-after-retr-error"
                scan_stop = "retr-deferred"
                break
            phase = 'message'
            content = base.canonical_message(lines, max_message_bytes)
            if reservation is not None:
                reservation.accept(len(content))
            selected.append(base.Message(number, uid, content))

        selected.sort(key=lambda message: message.server_number)
        if scan_state is not None:
            evidence.scan_state_after = pop_backlog.advance(scan_state, next_number)
        evidence.selected_messages = len(selected)
        evidence.selected_octets = sum(len(message.content) for message in selected)
    except base.BridgeConnectionAborted as exc:
        note_upstream_issue(evidence, phase, exc)
        connection_usable = False
        failure = exc
    except base.BridgeError as exc:
        note_upstream_issue(evidence, phase, exc)
        failure = exc
    except (OSError, ssl.SSLError, poplib.error_proto) as exc:
        note_upstream_issue(evidence, phase, exc)
        failure = base.BridgeError(f"upstream_{type(exc).__name__.lower()}")
    finally:
        evidence.retrieval_candidates_deferred = deferred
        evidence.provider_cleanup = provider_cleanup
        if client is not None and connection_usable:
            if authenticated:
                try:
                    evidence.commands.append("RSET")
                    client.rset()
                except Exception as exc:
                    note_upstream_issue(evidence, 'rset', exc)
                    if failure is None:
                        failure = base.BridgeError("upstream_rset_failed")
            try:
                evidence.commands.append("QUIT")
                client.quit()
            except Exception as exc:
                note_upstream_issue(evidence, 'quit', exc)
                try:
                    client.close()
                except Exception:
                    pass
                if failure is None:
                    failure = base.BridgeError("upstream_quit_failed")
        elif client is not None:
            try:
                client.close()
            except Exception:
                pass
    evidence.scan_stop = scan_stop if failure is None else "failed"
    if failure is not None:
        failure.upstream_diagnostic = upstream_diagnostic(evidence)
        raise failure
    return selected, evidence, deferred_uids


def broker_transaction(
    base: ModuleType,
    connection: socket.socket,
    args: argparse.Namespace,
    account: object,
    number: int,
    aggregate: BrokerAggregate,
) -> None:
    started = time.monotonic()
    evidence = base.FetchEvidence()
    category = "none"
    selected: list[object] = []
    connection.settimeout(args.request_timeout)
    try:
        if receive_exact(connection, len(REQUEST_MAGIC)) != REQUEST_MAGIC:
            raise SessionBridgeError("request_magic_invalid")
        username = receive_blob(connection, base.MAX_CREDENTIAL_BYTES)
        password = receive_blob(connection, base.MAX_CREDENTIAL_BYTES)
        known = base.read_known_uidls(args.known_uid_file)
        known.update(read_ledger(base, args.delivered_ledger))
        known.update(read_ledger(base, args.deferred_ledger))
        scan_path = getattr(args, 'scan_state', None)
        scan_state = pop_backlog.read(scan_path) if scan_path is not None else None
        budget_path = getattr(args, 'catchup_budget', None)
        scan_state = catchup_scan_state(scan_state, budget_path is not None)
        reservation = None if budget_path is None else pop_catchup_budget.IncrementalReservation.open(
            budget_path, args.batch_size, peers=4 if args.batch_size > 10 else 1)
        allowance = args.batch_size if reservation is None else reservation.slots
        # Admission point: already-admitted transactions on other accounts may
        # settle, but Agent retries must not start another provider transaction.
        if provider_disabled(args):
            raise SessionBridgeError('provider_access_disabled')
        selected, evidence, deferred_uids = fetch_batch_for_session(
            base,
            account,
            username,
            password,
            known,
            batch_size=allowance,
            scan_limit=args.scan_limit,
            max_message_bytes=args.max_message_bytes,
            timeout_seconds=args.upstream_timeout,
            evidence=evidence,
            scan_state=scan_state,
            reservation=reservation,
        )
        aggregate.retrieval_candidates_deferred += getattr(
            evidence, "retrieval_candidates_deferred", 0
        )
        if getattr(evidence, "provider_cleanup", "rset-quit") == "closed-after-retr-error":
            aggregate.retr_error_closed_connections += 1
        merge_ledger(base, args.deferred_ledger, deferred_uids)
        base.send_messages(connection, selected)
        committed = receive_commit(base, connection, selected)
        merge_ledger(base, args.delivered_ledger, committed)
        if reservation is not None:
            reservation.finish(len(selected),
                sum(len(message.content) for message in selected), len(deferred_uids))
        # An incomplete delivery must revisit the same positions. The UID ledger
        # skips its already-committed subset; no cursor ever establishes delivery.
        if scan_state is not None and committed == {message.uid for message in selected}:
            pop_backlog.write(scan_path, evidence.scan_state_after)
        connection.sendall(COMMIT_OK)
        aggregate.completed += 1
        aggregate.messages_selected += len(selected)
        aggregate.messages_committed += len(committed)
        aggregate.selected_octets += sum(len(message.content) for message in selected)
        status = "complete"
    except (SessionBridgeError, base.BridgeError, ValueError) as exc:
        disable_provider(args)
        category = safe_category(getattr(exc, "category", type(exc).__name__))
        aggregate.failed += 1
        status = "failed"
        try:
            base.send_failure(connection, getattr(exc, 'upstream_diagnostic', category))
        except OSError:
            pass
        raise SessionBridgeError(category) from exc
    except OSError as exc:
        disable_provider(args)
        category = safe_category(f"local_{type(exc).__name__}")
        aggregate.failed += 1
        status = "failed"
        raise SessionBridgeError(category) from exc
    finally:
        commands = ",".join(evidence.commands)
        text = "\n".join(
            [
                f"transaction={number}",
                f"status={locals().get('status', 'failed')}",
                f"failure_category={category}",
                f"commands_sent={commands}",
                f"candidates_scanned={evidence.candidates_scanned}",
                f"known_candidates_skipped={evidence.known_candidates_skipped}",
                f"oversized_candidates_skipped={evidence.oversized_candidates_skipped}",
                f"retrieval_candidates_deferred={getattr(evidence, 'retrieval_candidates_deferred', 0)}",
                f"provider_cleanup={getattr(evidence, 'provider_cleanup', 'rset-quit')}",
                f"scan_stop={getattr(evidence, 'scan_stop', 'legacy')}",
                f"upstream_issue_phase={getattr(evidence, 'upstream_issue_phase', 'none')}",
                f"upstream_issue_kind={getattr(evidence, 'upstream_issue_kind', 'none')}",
                f"upstream_login_completed={getattr(evidence, 'upstream_login_completed', 'unknown')}",
                f"upstream_response_hint={getattr(evidence, 'upstream_response_hint', 'none')}",
                f"upstream_stat_messages={diagnostic_number(getattr(evidence, 'upstream_stat_messages', 0))}",
                f"upstream_scan_start_number={diagnostic_number(getattr(evidence, 'upstream_scan_start_number', 0))}",
                f"upstream_last_uidl_number={diagnostic_number(getattr(evidence, 'upstream_last_uidl_number', 0))}",
                f"selected_messages={evidence.selected_messages}",
                f"selected_octets={evidence.selected_octets}",
                f"elapsed_seconds={time.monotonic() - started:.3f}",
                "delete_command_available=no",
                "uid_values_logged=no",
                "message_content_logged=no",
            ]
        ) + "\n"
        atomic_write(args.evidence_dir / f"transaction-{number:04d}-upstream.txt", text)


def broker_main(base: ModuleType, args: argparse.Namespace) -> int:
    socket_path: Path = args.socket
    aggregate = BrokerAggregate()
    stopping = False

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    try:
        base.validate_unix_socket_path(socket_path)
        if socket_path.exists() or socket_path.is_symlink():
            raise SessionBridgeError("broker_socket_exists")
        if not args.evidence_dir.is_dir() or args.evidence_dir.is_symlink():
            raise SessionBridgeError("evidence_directory_invalid")
        if provider_disabled(args):
            raise SessionBridgeError('provider_access_disabled_at_startup')
        read_ledger(base, args.delivered_ledger)
        read_ledger(base, args.deferred_ledger)
        if getattr(args, 'scan_state', None) is not None:
            pop_backlog.read(args.scan_state)
        account = base.parse_account(
            args.agent_ini,
            args.account_uid,
            args.expected_host,
            args.expected_port,
        )
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGHUP, stop)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.settimeout(1.0)
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(1)
        atomic_write(args.ready_file, "ready=yes\n")
        atomic_write(args.aggregate_log, aggregate.text("listening"))
        with server:
            while not stopping:
                if aggregate.attempts >= args.max_connections and not provider_disabled(args):
                    break
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                if provider_disabled(args):
                    # Remain alive for normal session shutdown. Exiting here
                    # would cause the supervisor to interrupt sibling commits.
                    with connection:
                        connection.settimeout(1.0)
                        try:
                            base.send_failure(connection, 'provider_access_disabled')
                        except OSError:
                            pass
                    continue
                aggregate.attempts += 1
                try:
                    with connection:
                        broker_transaction(
                            base, connection, args, account, aggregate.attempts, aggregate
                        )
                except SessionBridgeError:
                    disable_provider(args)
                    atomic_write(args.aggregate_log, aggregate.text("listening"))
                    continue
                atomic_write(args.aggregate_log, aggregate.text("listening"))
        atomic_write(args.aggregate_log, aggregate.text("stopped"))
        return 0
    except (OSError, SessionBridgeError, base.BridgeError, ValueError) as exc:
        aggregate.failed += 1
        atomic_write(args.aggregate_log, aggregate.text("failed"))
        print(f"NOMINAL-BROKER-SAFE-STOP: {safe_category(str(exc))}", file=sys.stderr)
        return 1
    finally:
        try:
            if socket_path.exists() or socket_path.is_symlink():
                socket_path.unlink()
        except OSError:
            pass


@dataclasses.dataclass
class ListenerAggregate:
    connections: int = 0
    completed: int = 0
    failed: int = 0
    messages_loaded: int = 0
    messages_committed: int = 0
    content_responses: int = 0
    destructive_rejections: int = 0
    counts: collections.Counter[str] = dataclasses.field(default_factory=collections.Counter)

    def text(self, status: str) -> str:
        lines = [
            f"status={status}",
            f"updated_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"connections={self.connections}",
            f"transactions_completed={self.completed}",
            f"transactions_failed={self.failed}",
            f"messages_loaded={self.messages_loaded}",
            f"messages_committed={self.messages_committed}",
            f"content_responses_served={self.content_responses}",
            f"destructive_commands_rejected={self.destructive_rejections}",
            "uid_values_logged=no",
            "message_content_logged=no",
        ]
        for command in sorted(self.counts):
            lines.append(f"command_{command}={self.counts[command]}")
        return "\n".join(lines) + "\n"


def send_line(stream, value: bytes) -> None:
    stream.write(value + b"\r\n")


def send_multiline(stream, lines: list[bytes]) -> None:
    for line in lines:
        if line.startswith(b"."):
            line = b"." + line
        send_line(stream, line)
    send_line(stream, b".")
    stream.flush()


def parse_index(argument: bytes, count: int) -> int | None:
    fields = argument.split()
    if len(fields) != 1 or not fields[0].isdigit():
        return None
    index = int(fields[0])
    return index if 1 <= index <= count else None


def content_lines(content: bytes) -> list[bytes]:
    if not content.endswith(b"\r\n"):
        raise SessionBridgeError("local_message_not_canonical")
    return content[:-2].split(b"\r\n")


def open_broker(
    base: ModuleType,
    socket_path: Path,
    username: bytes,
    password: bytes,
    max_message_bytes: int,
) -> tuple[socket.socket, list[object]]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(600)
    try:
        connection.connect(str(socket_path))
        connection.sendall(REQUEST_MAGIC)
        send_blob(connection, username)
        send_blob(connection, password)
        messages = base.receive_messages(connection, max_message_bytes)
        return connection, messages
    except Exception:
        connection.close()
        raise


def handle_agent_connection(
    base: ModuleType,
    connection: socket.socket,
    args: argparse.Namespace,
    aggregate: ListenerAggregate,
) -> None:
    aggregate.connections += 1
    atomic_write(args.aggregate_log, aggregate.text("serving"))
    broker: socket.socket | None = None
    messages: list[object] = []
    retrieved: set[int] = set()
    topped: set[int] = set()
    authenticated = False
    authentication_attempted = False
    quit_received = False
    current_user = b""
    connection.settimeout(args.client_timeout)
    try:
        with connection:
            stream = connection.makefile("rwb", buffering=64 * 1024)
            send_line(stream, b"+OK persistent bounded Forte Agent POP bridge")
            stream.flush()
            while True:
                raw = stream.readline(16 * 1024)
                if not raw:
                    break
                parts = raw.rstrip(b"\r\n").split(None, 1)
                if not parts:
                    continue
                try:
                    command = parts[0].decode("ascii").upper()
                except UnicodeDecodeError:
                    command = "NONASCII"
                argument = parts[1].strip() if len(parts) == 2 else b""
                aggregate.counts[command] += 1
                atomic_write(args.aggregate_log, aggregate.text("serving"))

                if command == "CAPA":
                    send_line(stream, b"+OK capability list follows")
                    send_multiline(stream, [b"USER", b"UIDL", b"TOP", b"IMPLEMENTATION nominal-session-bridge"])
                elif command == "USER":
                    if not 1 <= len(argument) <= base.MAX_CREDENTIAL_BYTES:
                        send_line(stream, b"-ERR invalid USER")
                    else:
                        current_user = argument
                        send_line(stream, b"+OK user accepted")
                elif command == "PASS":
                    if authentication_attempted:
                        send_line(stream, b"-ERR already authenticated")
                    elif not current_user or not 1 <= len(argument) <= base.MAX_CREDENTIAL_BYTES:
                        send_line(stream, b"-ERR USER required before PASS")
                    else:
                        authentication_attempted = True
                        try:
                            broker, messages = open_broker(
                                base, args.socket, current_user, argument, args.max_message_bytes
                            )
                            authenticated = True
                            aggregate.messages_loaded += len(messages)
                            send_line(stream, b"+OK bounded local maildrop ready")
                        except (OSError, SessionBridgeError, base.BridgeError) as exc:
                            aggregate.failed += 1
                            send_line(stream, bridge_error_reply(exc))
                elif command == "QUIT":
                    send_line(stream, b"+OK bounded bridge closing")
                    stream.flush()
                    quit_received = True
                    break
                elif not authenticated:
                    send_line(stream, b"-ERR authenticate first")
                elif command == "STAT":
                    total = sum(len(message.content) for message in messages)
                    send_line(stream, f"+OK {len(messages)} {total}".encode("ascii"))
                elif command == "LIST":
                    if argument:
                        index = parse_index(argument, len(messages))
                        if index is None:
                            send_line(stream, b"-ERR no such message")
                        else:
                            send_line(stream, f"+OK {index} {len(messages[index - 1].content)}".encode("ascii"))
                    else:
                        send_line(stream, b"+OK scan listing follows")
                        send_multiline(stream, [
                            f"{index} {len(message.content)}".encode("ascii")
                            for index, message in enumerate(messages, start=1)
                        ])
                elif command == "UIDL":
                    if argument:
                        index = parse_index(argument, len(messages))
                        if index is None:
                            send_line(stream, b"-ERR no such message")
                        else:
                            send_line(stream, b"+OK " + str(index).encode() + b" " + messages[index - 1].uid)
                    else:
                        send_line(stream, b"+OK unique-id listing follows")
                        send_multiline(stream, [
                            str(index).encode() + b" " + message.uid
                            for index, message in enumerate(messages, start=1)
                        ])
                elif command == "RETR":
                    index = parse_index(argument, len(messages))
                    if index is None:
                        send_line(stream, b"-ERR no such message")
                    elif index in retrieved:
                        send_line(stream, b"-ERR RETR content limit reached")
                    else:
                        message = messages[index - 1]
                        send_line(stream, f"+OK {len(message.content)} octets".encode("ascii"))
                        send_multiline(stream, content_lines(message.content))
                        retrieved.add(index)
                        aggregate.content_responses += 1
                elif command == "TOP":
                    fields = argument.split()
                    if len(fields) != 2 or not fields[0].isdigit() or not fields[1].isdigit():
                        send_line(stream, b"-ERR invalid TOP")
                    else:
                        index, body_count = int(fields[0]), int(fields[1])
                        if not 1 <= index <= len(messages):
                            send_line(stream, b"-ERR no such message")
                        elif index in topped:
                            send_line(stream, b"-ERR TOP content limit reached")
                        else:
                            lines = content_lines(messages[index - 1].content)
                            separator = lines.index(b"") if b"" in lines else len(lines)
                            subset = lines[: separator + 1] + lines[separator + 1 : separator + 1 + body_count]
                            send_line(stream, b"+OK top of message follows")
                            send_multiline(stream, subset)
                            topped.add(index)
                            aggregate.content_responses += 1
                elif command == "DELE":
                    aggregate.destructive_rejections += 1
                    send_line(stream, b"-ERR DELE disabled by persistent bridge")
                elif command in {"NOOP", "RSET"}:
                    send_line(stream, b"+OK")
                elif command == "LAST":
                    send_line(stream, b"+OK 0")
                else:
                    send_line(stream, b"-ERR unsupported command")
                stream.flush()
    finally:
        if broker is not None:
            # A complete RETR is not durable POP delivery until the client also
            # completes the transaction with QUIT. On a crash or disconnect,
            # commit nothing so a later poll can offer the messages again.
            committed = (
                {messages[index - 1].uid for index in retrieved}
                if quit_received
                else set()
            )
            try:
                send_commit(broker, committed)
                if receive_exact(broker, len(COMMIT_OK)) != COMMIT_OK:
                    raise SessionBridgeError("commit_ack_invalid")
                aggregate.messages_committed += len(committed)
                aggregate.completed += 1
            finally:
                broker.close()
        atomic_write(args.aggregate_log, aggregate.text("listening"))


def listener_main(base: ModuleType, args: argparse.Namespace) -> int:
    aggregate = ListenerAggregate()
    stopping = False

    def stop(_signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    try:
        base.validate_unix_socket_path(args.socket)
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGHUP, stop)
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((args.listen_host, args.port))
        server.listen(4)
        server.settimeout(1.0)
        atomic_write(args.ready_file, "ready=yes\n")
        atomic_write(args.aggregate_log, aggregate.text("listening"))
        with server:
            while not stopping and aggregate.connections < args.max_connections:
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                try:
                    handle_agent_connection(base, connection, args, aggregate)
                except (OSError, SessionBridgeError, base.BridgeError):
                    aggregate.failed += 1
                    atomic_write(args.aggregate_log, aggregate.text("failed"))
                    return 1
        atomic_write(args.aggregate_log, aggregate.text("stopped"))
        return 0
    except (OSError, SessionBridgeError, base.BridgeError) as exc:
        aggregate.failed += 1
        atomic_write(args.aggregate_log, aggregate.text("failed"))
        print(f"NOMINAL-LISTENER-SAFE-STOP: {safe_category(str(exc))}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-bridge",
        type=Path,
        default=Path(__file__).with_name("pop-bounded-bridge.py"),
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    broker = subparsers.add_parser("broker")
    broker.add_argument("--socket", required=True, type=Path)
    broker.add_argument("--ready-file", required=True, type=Path)
    broker.add_argument("--aggregate-log", required=True, type=Path)
    broker.add_argument("--evidence-dir", required=True, type=Path)
    broker.add_argument("--agent-ini", required=True, type=Path)
    broker.add_argument("--known-uid-file", required=True, type=Path)
    broker.add_argument("--delivered-ledger", required=True, type=Path)
    broker.add_argument("--deferred-ledger", required=True, type=Path)
    broker.add_argument("--scan-state", type=Path)
    broker.add_argument("--catchup-budget", type=Path)
    broker.add_argument("--account-uid", required=True, type=int)
    broker.add_argument("--expected-host", required=True)
    broker.add_argument("--expected-port", required=True, type=int)
    broker.add_argument("--batch-size", type=int, default=10)
    broker.add_argument("--scan-limit", type=int, default=1000)
    broker.add_argument("--max-message-bytes", type=int, default=10 * 1024 * 1024)
    broker.add_argument("--max-connections", type=int, default=MAX_SESSION_CONNECTIONS)
    broker.add_argument("--upstream-timeout", type=float, default=180)
    broker.add_argument("--request-timeout", type=float, default=900)

    listener = subparsers.add_parser("listener")
    listener.add_argument("--socket", required=True, type=Path)
    listener.add_argument("--ready-file", required=True, type=Path)
    listener.add_argument("--aggregate-log", required=True, type=Path)
    listener.add_argument("--listen-host", default="127.0.0.1")
    listener.add_argument("--port", required=True, type=int)
    listener.add_argument("--max-message-bytes", type=int, default=10 * 1024 * 1024)
    listener.add_argument("--max-connections", type=int, default=MAX_SESSION_CONNECTIONS)
    listener.add_argument("--client-timeout", type=float, default=300)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.max_connections <= MAX_SESSION_CONNECTIONS:
        raise SessionBridgeError("connection_bound_invalid")
    if not 1024 <= args.max_message_bytes <= 10 * 1024 * 1024:
        raise SessionBridgeError("message_bound_invalid")
    if args.mode == "broker":
        if not 1 <= args.batch_size <= MAX_BATCH_MESSAGES:
            raise SessionBridgeError("batch_bound_invalid")
        if args.batch_size > 10 and getattr(args, "catchup_budget", None) is None:
            raise SessionBridgeError("large_batch_requires_catchup_budget")
        if not 1 <= args.scan_limit <= 5000:
            raise SessionBridgeError("scan_bound_invalid")
        if not 1 <= args.expected_port <= 65535:
            raise SessionBridgeError("provider_port_invalid")
    else:
        if args.listen_host != "127.0.0.1":
            raise SessionBridgeError("listener_not_loopback")
        if not 1024 <= args.port <= 65535:
            raise SessionBridgeError("listener_port_invalid")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        base = load_base_bridge(args.base_bridge)
        if args.mode == "broker":
            return broker_main(base, args)
        return listener_main(base, args)
    except SessionBridgeError as exc:
        print(f"NOMINAL-SESSION-BRIDGE-SAFE-STOP: {exc.category}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit('INTERNAL MODULE: use the portable debian_launcher.py entrypoint.')
