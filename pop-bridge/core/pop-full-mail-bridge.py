#!/usr/bin/env python3
"""Full UID inventory and on-demand bodies; no POP deletion operation exists.

This additive bridge reuses the accepted service loops and protected UID ledger.
It has a different local wire protocol from the small-batch bridge. A transaction
offers all untracked, eligible mail within explicit resource bounds. On another
poll the full inventory plus committed UIDs resumes without message-number cursors.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import os
from pathlib import Path
import poplib
import re
import socket
import ssl
import struct
import sys
import tempfile
import time


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


C = load('full_mail_core', 'pop-nominal-session-bridge.py')
MAX_ROWS = 100_000
MAX_METADATA_BYTES = 8 * 1024 * 1024
MAX_MESSAGES = 1000
MAX_BYTES = 256 * 1024 * 1024
MAX_MESSAGE = 10 * 1024 * 1024
MAX_SECONDS = 3600
REQUEST = b'FAFM1'
FETCH = b'FAFR1'
COMMIT = b'FAFC1'
ALLOWED = frozenset(('USER', 'PASS', 'STAT', 'UIDL', 'LIST', 'RETR', 'RSET', 'QUIT'))


def need(ok, category):
    if not ok:
        raise C.SessionBridgeError(category)


class FullPOP(poplib.POP3_SSL):
    """Verified TLS, command allowlist, bounded bulk metadata and body lines."""
    def __init__(self, host, port, *, context, timeout):
        need(context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED,
             'tls_verification_required')
        self.deadline = time.monotonic() + MAX_SECONDS
        super().__init__(host, port, context=context, timeout=timeout)

    def _putcmd(self, line):
        need(line.split(' ', 1)[0] in ALLOWED, 'forbidden_command')
        need('\r' not in line and '\n' not in line, 'command_injection')
        need(time.monotonic() < self.deadline, 'work_deadline')
        super()._putcmd(line)

    def _getlongresp(self):
        # Used only by whole-mailbox UIDL/LIST. RETR uses retr_bounded instead.
        response = self._getresp()
        lines, total = [], 0
        while True:
            need(time.monotonic() < self.deadline, 'work_deadline')
            line, octets = self._getline()
            total += octets
            need(total <= MAX_METADATA_BYTES, 'metadata_byte_limit')
            if line == b'.':
                return response, lines, total
            need(len(lines) < MAX_ROWS and not line.startswith(b'.'), 'metadata_row_limit')
            lines.append(line)


def parse_rows(lines, count, kind):
    need(kind in ('uidl', 'list') and type(count) is int and 0 <= count <= MAX_ROWS,
         'metadata_kind_or_count_invalid')
    need(len(lines) == count, 'incomplete_metadata')
    result = {}
    for line in lines:
        match = re.fullmatch(rb'([1-9][0-9]{0,5}) ([\x21-\x7e]{1,4096})', line)
        need(match is not None, 'invalid_metadata_row')
        number, value = int(match[1]), match[2]
        need(number <= count and number not in result, 'metadata_number_invalid')
        if kind == 'list':
            need(re.fullmatch(rb'0|[1-9][0-9]{0,18}', value) is not None, 'message_size_invalid')
            value = int(value)
            need(value < 2**63, 'message_size_invalid')
        result[number] = value
    need(set(result) == set(range(1, count + 1)), 'incomplete_metadata')
    if kind == 'uidl':
        need(len(set(result.values())) == count, 'duplicate_uid')
    return result


def valid_stat(value):
    need(type(value) is tuple and len(value) == 2, 'stat_invalid')
    count, size = value
    need(type(count) is int and 0 <= count <= MAX_ROWS and
         type(size) is int and 0 <= size < 2**63, 'stat_invalid')
    return value


@dataclasses.dataclass
class Offer:
    server_number: int
    uid: bytes
    size: int


def choose(uid_rows, sizes, known, deferred):
    """Newest-number first selection, oldest-first display; no arrival-date claim."""
    offered, octets, unknown, oversized, refused = [], 0, 0, 0, 0
    for number in sorted(uid_rows, reverse=True):
        uid, size = uid_rows[number], sizes[number]
        if uid in known:
            continue
        unknown += 1
        if size > MAX_MESSAGE:
            oversized += 1
        elif uid in deferred:
            refused += 1
        elif len(offered) < MAX_MESSAGES and octets + size <= MAX_BYTES:
            offered.append(Offer(number, uid, size))
            octets += size
    offered.reverse()
    return offered, dict(server_messages=len(uid_rows), untracked_messages=unknown,
                         oversized_messages=oversized, deferred_messages=refused,
                         offered_messages=len(offered), offered_octets=octets,
                         resource_paused_messages=unknown-oversized-refused-len(offered))


def publish_ledger(base, path, committed):
    values = C.read_ledger(base, path)
    need(all(base.valid_uid(uid) for uid in committed), 'commit_uid_invalid')
    values.update(committed)
    descriptor, name = tempfile.mkstemp(prefix='.full-mail-ledger-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            for uid in sorted(values):
                stream.write(uid + b'\n')
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()  # Only this function's unique staging file.


def broker_transaction(base, connection, args, account, number, aggregate, *, factory=FullPOP):
    """One TLS session, snapshot inventory, fetch requests, then QUIT-only commit."""
    started = time.monotonic()
    client = None
    offered, fetched, committed, commands = [], set(), set(), []
    actual_octets = 0
    ledger_published = ledger_durable = False
    summary = {}
    status, category, phase = 'failed', 'none', 'local-request'
    login = False
    connection.settimeout(args.request_timeout)
    report = args.evidence_dir / f'transaction-{number:04d}-upstream.txt'
    try:
        need(C.receive_exact(connection, 5) == REQUEST, 'full_mail_protocol_required')
        username = C.receive_blob(connection, base.MAX_CREDENTIAL_BYTES)
        password = C.receive_blob(connection, base.MAX_CREDENTIAL_BYTES)
        need(username == account.username, 'agent_username_mismatch')
        need(1 <= len(password) <= base.MAX_CREDENTIAL_BYTES and
             all(0x20 <= b <= 0x7e for b in password), 'credential_invalid')
        need(not C.provider_disabled(args), 'provider_access_disabled')
        known = base.read_known_uidls(args.known_uid_file)
        known.update(C.read_ledger(base, args.delivered_ledger))
        deferred = C.read_ledger(base, args.deferred_ledger)
        phase = 'connect'
        client = factory(account.host, account.port, context=ssl.create_default_context(),
                         timeout=args.upstream_timeout)
        phase = 'user'; commands.append('USER'); client.user(username.decode('ascii'))
        phase = 'pass'; commands.append('PASS'); client.pass_(password.decode('ascii'))
        login = True
        phase = 'stat'; commands.append('STAT'); before = valid_stat(client.stat())
        phase = 'uidl'; commands.append('UIDL'); rows = parse_rows(client.uidl()[1], before[0], 'uidl')
        phase = 'list'; commands.append('LIST'); sizes = parse_rows(client.list()[1], before[0], 'list')
        need(sum(sizes.values()) == before[1], 'stat_list_size_mismatch')
        phase = 'stat'; commands.append('STAT'); need(valid_stat(client.stat()) == before, 'maildrop_changed')
        offered, summary = choose(rows, sizes, known, deferred)
        # Only numeric public evidence; no credentials, UID values or content.
        C.atomic_write(args.evidence_dir / f'transaction-{number:04d}-inventory.txt',
                       ''.join(f'{k}={v}\n' for k, v in summary.items()))
        connection.sendall(b'O' + struct.pack('!I', len(offered)))
        for item in offered:
            C.send_blob(connection, item.uid)
            connection.sendall(struct.pack('!Q', item.size))
        while True:
            phase = 'local-request'
            operation = C.receive_exact(connection, 5)
            if operation == COMMIT:
                count = struct.unpack('!I', C.receive_exact(connection, 4))[0]
                need(count <= len(fetched), 'commit_count_invalid')
                fetched_uids = {offered[i-1].uid for i in fetched}
                for _ in range(count):
                    uid = C.receive_blob(connection, base.MAX_UID_BYTES)
                    need(uid in fetched_uids and uid not in committed, 'commit_uid_invalid')
                    committed.add(uid)
                break
            need(operation == FETCH, 'operation_invalid')
            index = struct.unpack('!I', C.receive_exact(connection, 4))[0]
            need(1 <= index <= len(offered) and index not in fetched, 'fetch_index_invalid')
            need(time.monotonic() - started < MAX_SECONDS, 'work_deadline')
            item = offered[index-1]
            # Verify UID-to-number mapping immediately before RETR, including
            # providers that violate a stable POP transaction snapshot.
            phase = 'uidl'; commands.append('UIDL')
            need(base.parse_uidl_response(client.uidl(item.server_number), item.server_number) == item.uid,
                 'message_identity_changed')
            phase = 'retr'; commands.append('RETR')
            _, lines, _ = base.retr_bounded(
                C.BoundedRetrReader(client, MAX_MESSAGE, base.BridgeConnectionAborted),
                item.server_number, MAX_MESSAGE)
            body = base.canonical_message(lines, MAX_MESSAGE)
            need(actual_octets + len(body) <= MAX_BYTES, 'content_byte_limit')
            actual_octets += len(body)
            connection.sendall(b'O'); C.send_blob(connection, body)
            fetched.add(index)
        phase = 'rset'; commands.append('RSET'); client.rset()
        phase = 'quit'; commands.append('QUIT'); client.quit()
        client = None
        publish_ledger(base, args.delivered_ledger, committed)
        ledger_published = True
        descriptor = os.open(args.delivered_ledger.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        ledger_durable = True
        connection.sendall(C.COMMIT_OK)
        aggregate.completed += 1
        aggregate.messages_selected += len(offered)
        aggregate.messages_committed += len(committed)
        aggregate.selected_octets += actual_octets
        status = 'complete'
    except Exception as exc:
        C.disable_provider(args)
        aggregate.failed += 1
        category = exc.category if isinstance(exc, C.SessionBridgeError) else type(exc).__name__.lower()
        try:
            base.send_failure(connection, 'full_mail_' + category)
        except OSError:
            pass
        raise C.SessionBridgeError('full_mail_' + category) from None
    finally:
        if client is not None:
            try:
                client.close()  # No DELE exists; do not resynchronize an uncertain stream.
            except Exception:
                pass
        data = dict(transaction=number, status=status, failure_category=category,
                    protocol='full-mail-v1', commands_sent=','.join(commands),
                    selected_messages=len(offered), fetched_messages=len(fetched),
                    committed_messages=len(committed) if ledger_published else 0,
                    ledger_published='yes' if ledger_published else 'no',
                    ledger_durable='yes' if ledger_durable else 'no',
                    selected_octets=actual_octets, retrieval_candidates_deferred=0,
                    provider_cleanup='rset-quit' if status == 'complete' else 'closed-after-error',
                    issue_phase=phase if status != 'complete' else 'none',
                    login_completed='yes' if login else 'no',
                    remaining_untracked_messages=summary.get('untracked_messages', 0)-(len(committed) if ledger_published else 0),
                    inventory_complete='yes' if summary else 'no',
                    delete_command_available='no', uid_values_logged='no', message_content_logged='no',
                    elapsed_seconds=f'{time.monotonic()-started:.3f}', **summary)
        C.atomic_write(report, ''.join(f'{k}={v}\n' for k, v in data.items()))


@dataclasses.dataclass
class LocalMessage:
    uid: bytes
    size: int
    index: int
    broker: socket.socket
    cache: Path

    @property
    def content(self):
        if not self.cache.exists():
            self.broker.sendall(FETCH + struct.pack('!I', self.index))
            status = C.receive_exact(self.broker, 1)
            if status == b'E':
                C.receive_blob(self.broker, 128)
                raise C.SessionBridgeError('upstream_body_failed')
            need(status == b'O', 'body_status_invalid')
            body = C.receive_blob(self.broker, MAX_MESSAGE)
            need(body.endswith(b'\r\n'), 'body_not_canonical')
            with self.cache.open('xb') as stream:
                os.chmod(self.cache, 0o600)
                stream.write(body)
        return self.cache.read_bytes()


def open_broker(base, socket_path, username, password, cache):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(300)
    try:
        connection.connect(str(socket_path))
        connection.sendall(REQUEST)
        C.send_blob(connection, username); C.send_blob(connection, password)
        status = C.receive_exact(connection, 1)
        if status == b'E':
            C.receive_blob(connection, 128)
            raise C.SessionBridgeError('upstream_inventory_failed')
        need(status == b'O', 'inventory_status_invalid')
        count = struct.unpack('!I', C.receive_exact(connection, 4))[0]
        need(count <= MAX_MESSAGES, 'offer_count_invalid')
        messages, ids, octets = [], set(), 0
        for i in range(1, count+1):
            uid = C.receive_blob(connection, base.MAX_UID_BYTES)
            size = struct.unpack('!Q', C.receive_exact(connection, 8))[0]
            octets += size
            need(base.valid_uid(uid) and uid not in ids and size <= MAX_MESSAGE and octets <= MAX_BYTES,
                 'offer_metadata_invalid')
            ids.add(uid)
            messages.append(LocalMessage(uid, size, i, connection, cache / str(i)))
        return connection, messages
    except Exception:
        connection.close()
        raise


def send_commit(connection, committed):
    connection.sendall(COMMIT + struct.pack('!I', len(committed)))
    for uid in sorted(committed):
        C.send_blob(connection, uid)
    need(C.receive_exact(connection, len(C.COMMIT_OK)) == C.COMMIT_OK, 'commit_ack_invalid')


def validate_args(args):
    need(1 <= args.max_connections <= 10 and args.max_message_bytes == MAX_MESSAGE, 'limits_invalid')
    if args.mode == 'broker':
        need(args.batch_size == MAX_MESSAGES and args.scan_state is None and args.catchup_budget is None,
             'full_mail_mode_required')
        need(args.scan_limit == MAX_ROWS, 'inventory_limit_invalid')
        need(1 <= args.upstream_timeout <= 180 and 1 <= args.request_timeout <= MAX_SECONDS,
             'timeout_invalid')
    else:
        need(args.listen_host == '127.0.0.1' and 1024 <= args.port <= 65535, 'listener_invalid')


def main():
    os.umask(0o077)
    args = C.build_parser().parse_args()
    validate_args(args)
    base = C.load_base_bridge(args.base_bridge)
    C.broker_transaction = broker_transaction
    C.handle_agent_connection = handle_agent_connection
    return C.broker_main(base, args) if args.mode == 'broker' else C.listener_main(base, args)


# The listener handler below is derived from the accepted small-batch handler;
# STAT/LIST read metadata, bodies are fetched lazily, and QUIT waits for the
# durable broker acknowledgement before Agent is told the transaction finished.


def handle_agent_connection(base, connection, args, aggregate):
    # Per-connection tool-owned cache, never a user message-store directory.
    with tempfile.TemporaryDirectory(prefix='full-mail-cache-', dir=args.aggregate_log.parent) as directory:
        _handle_agent_connection(base, connection, args, aggregate, Path(directory))


def _handle_agent_connection(
    base: ModuleType,
    connection: socket.socket,
    args,
    aggregate: C.ListenerAggregate,
    cache: Path,
) -> None:
    aggregate.connections += 1
    C.atomic_write(args.aggregate_log, aggregate.text("serving"))
    broker: socket.socket | None = None
    messages: list[object] = []
    retrieved: set[int] = set()
    topped: set[int] = set()
    authenticated = False
    authentication_attempted = False
    quit_received = False
    commit_started = False
    current_user = b""
    connection.settimeout(args.client_timeout)
    try:
        with connection, connection.makefile("rwb", buffering=64 * 1024) as stream:
            C.send_line(stream, b"+OK persistent bounded Forte Agent POP bridge")
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
                C.atomic_write(args.aggregate_log, aggregate.text("serving"))

                if command == "CAPA":
                    C.send_line(stream, b"+OK capability list follows")
                    C.send_multiline(stream, [b"USER", b"UIDL", b"TOP", b"IMPLEMENTATION nominal-session-bridge"])
                elif command == "USER":
                    if not 1 <= len(argument) <= base.MAX_CREDENTIAL_BYTES:
                        C.send_line(stream, b"-ERR invalid USER")
                    else:
                        current_user = argument
                        C.send_line(stream, b"+OK user accepted")
                elif command == "PASS":
                    if authentication_attempted:
                        C.send_line(stream, b"-ERR already authenticated")
                    elif not current_user or not 1 <= len(argument) <= base.MAX_CREDENTIAL_BYTES:
                        C.send_line(stream, b"-ERR USER required before PASS")
                    else:
                        authentication_attempted = True
                        try:
                            broker, messages = open_broker(
                                base, args.socket, current_user, argument, cache
                            )
                            authenticated = True
                            aggregate.messages_loaded += len(messages)
                            C.send_line(stream, b"+OK bounded local maildrop ready")
                        except (OSError, C.SessionBridgeError, base.BridgeError) as exc:
                            aggregate.failed += 1
                            C.send_line(stream, C.bridge_error_reply(exc))
                elif command == "QUIT":
                    if broker is not None:
                        committed = {messages[i - 1].uid for i in retrieved}
                        commit_started = True
                        send_commit(broker, committed)
                        aggregate.messages_committed += len(committed)
                        aggregate.completed += 1
                        broker.close()
                        broker = None
                    C.send_line(stream, b"+OK bounded bridge closing")
                    stream.flush()
                    quit_received = True
                    break
                elif not authenticated:
                    C.send_line(stream, b"-ERR authenticate first")
                elif command == "STAT":
                    total = sum(message.size for message in messages)
                    C.send_line(stream, f"+OK {len(messages)} {total}".encode("ascii"))
                elif command == "LIST":
                    if argument:
                        index = C.parse_index(argument, len(messages))
                        if index is None:
                            C.send_line(stream, b"-ERR no such message")
                        else:
                            C.send_line(stream, f"+OK {index} {messages[index - 1].size}".encode("ascii"))
                    else:
                        C.send_line(stream, b"+OK scan listing follows")
                        C.send_multiline(stream, [
                            f"{index} {message.size}".encode("ascii")
                            for index, message in enumerate(messages, start=1)
                        ])
                elif command == "UIDL":
                    if argument:
                        index = C.parse_index(argument, len(messages))
                        if index is None:
                            C.send_line(stream, b"-ERR no such message")
                        else:
                            C.send_line(stream, b"+OK " + str(index).encode() + b" " + messages[index - 1].uid)
                    else:
                        C.send_line(stream, b"+OK unique-id listing follows")
                        C.send_multiline(stream, [
                            str(index).encode() + b" " + message.uid
                            for index, message in enumerate(messages, start=1)
                        ])
                elif command == "RETR":
                    index = C.parse_index(argument, len(messages))
                    if index is None:
                        C.send_line(stream, b"-ERR no such message")
                    elif index in retrieved:
                        C.send_line(stream, b"-ERR RETR content limit reached")
                    else:
                        message = messages[index - 1]
                        C.send_line(stream, f"+OK {len(message.content)} octets".encode("ascii"))
                        C.send_multiline(stream, C.content_lines(message.content))
                        retrieved.add(index)
                        aggregate.content_responses += 1
                elif command == "TOP":
                    fields = argument.split()
                    if len(fields) != 2 or not fields[0].isdigit() or not fields[1].isdigit():
                        C.send_line(stream, b"-ERR invalid TOP")
                    else:
                        index, body_count = int(fields[0]), int(fields[1])
                        if not 1 <= index <= len(messages):
                            C.send_line(stream, b"-ERR no such message")
                        elif index in topped:
                            C.send_line(stream, b"-ERR TOP content limit reached")
                        else:
                            lines = C.content_lines(messages[index - 1].content)
                            separator = lines.index(b"") if b"" in lines else len(lines)
                            subset = lines[: separator + 1] + lines[separator + 1 : separator + 1 + body_count]
                            C.send_line(stream, b"+OK top of message follows")
                            C.send_multiline(stream, subset)
                            topped.add(index)
                            aggregate.content_responses += 1
                elif command == "DELE":
                    aggregate.destructive_rejections += 1
                    C.send_line(stream, b"-ERR DELE disabled by persistent bridge")
                elif command in {"NOOP", "RSET"}:
                    C.send_line(stream, b"+OK")
                elif command == "LAST":
                    C.send_line(stream, b"+OK 0")
                else:
                    C.send_line(stream, b"-ERR unsupported command")
                stream.flush()
    finally:
        if broker is not None:
            try:
                # Disconnected client: acknowledge nothing, preserve resumability.
                if not commit_started:
                    send_commit(broker, set())
                    aggregate.completed += 1
            finally:
                broker.close()
        C.atomic_write(args.aggregate_log, aggregate.text("listening"))

if __name__ == '__main__':
    raise SystemExit('INTERNAL MODULE: use the portable debian_launcher.py entrypoint.')
