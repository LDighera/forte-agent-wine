#!/usr/bin/env python3
"""Bounded POP bridge for Forte Agent.

The loopback listener receives Agent's decoded USER/PASS commands inside an
isolated network namespace.  It passes those credentials in memory over a
private Unix socket to the upstream half.  The upstream half uses verified
TLS, selects only a bounded number of UIDLs not known to the accepted Agent
profile, retrieves bounded messages, issues RSET and QUIT, and has no DELE
code path.  Credentials, UIDLs, and message content are never logged.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import html
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
from typing import Callable


PROTOCOL_MAGIC = b"FAPB1"
MAX_CREDENTIAL_BYTES = 4096
MAX_UID_BYTES = 4096
MAX_UNIX_SOCKET_PATH_BYTES = 107
MAX_LOCAL_CONNECTIONS = 1


class BridgeError(Exception):
    """A failure whose short category is safe to record."""

    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


class BridgeConnectionAborted(BridgeError):
    """A bounded failure after which the upstream connection is unusable."""


@dataclasses.dataclass(frozen=True)
class Account:
    uid: int
    host: str
    port: int
    username: bytes


@dataclasses.dataclass(frozen=True)
class Message:
    server_number: int
    uid: bytes
    content: bytes


@dataclasses.dataclass
class FetchEvidence:
    server_message_count: int = 0
    server_message_octets: int = 0
    candidates_scanned: int = 0
    known_candidates_skipped: int = 0
    oversized_candidates_skipped: int = 0
    selected_messages: int = 0
    selected_octets: int = 0
    commands: list[str] = dataclasses.field(default_factory=list)


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def validate_unix_socket_path(path: Path) -> None:
    """Require room for Linux's terminating NUL in sockaddr_un.sun_path."""
    if len(os.fsencode(path)) > MAX_UNIX_SOCKET_PATH_BYTES:
        raise BridgeError("unix_socket_path_too_long")


def parse_account(
    agent_ini: Path,
    account_uid: int,
    expected_host: str,
    expected_port: int,
) -> Account:
    text = agent_ini.read_text(encoding="utf-8-sig")
    pop_match = re.search(
        r"<PopServers>\s*(.*?)\s*</PopServers>",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if pop_match is None:
        raise BridgeError("pop_servers_missing")

    matches: list[dict[str, str]] = []
    for body in re.findall(
        r"<Server>\s*(.*?)\s*</Server>",
        pop_match.group(1),
        re.IGNORECASE | re.DOTALL,
    ):
        fields: dict[str, str] = {}
        for key, value in re.findall(
            r"<([A-Za-z_][A-Za-z0-9_.:-]*)>(.*?)</\1>",
            body,
            re.IGNORECASE | re.DOTALL,
        ):
            fields[key.lower()] = html.unescape(value.strip())
        if fields.get("uid", "") == str(account_uid):
            matches.append(fields)

    if len(matches) != 1:
        raise BridgeError("account_uid_ambiguous")
    fields = matches[0]
    if fields.get("host", "").lower() != expected_host.lower():
        raise BridgeError("account_host_mismatch")
    if not parse_bool(fields.get("usessl", "")):
        raise BridgeError("upstream_tls_required")
    if not fields.get("username", ""):
        raise BridgeError("account_username_missing")

    if parse_bool(fields.get("usecustomport", "")):
        raw_port = fields.get("port", "")
        if not raw_port.isdigit():
            raise BridgeError("account_port_invalid")
        port = int(raw_port)
    else:
        port = 995
    if not 1 <= port <= 65535:
        raise BridgeError("account_port_invalid")
    if port != expected_port:
        raise BridgeError("account_port_mismatch")

    try:
        username = fields["username"].encode("ascii")
    except UnicodeEncodeError as exc:
        raise BridgeError("account_username_not_ascii") from exc
    if not 1 <= len(username) <= MAX_CREDENTIAL_BYTES:
        raise BridgeError("account_username_length_invalid")
    return Account(account_uid, fields["host"], port, username)


def read_known_uidls(*paths: Path) -> set[bytes]:
    values: set[bytes] = set()
    for path in paths:
        if not path.exists():
            continue
        if not path.is_file() or path.is_symlink():
            raise BridgeError("known_uid_state_invalid")
        for value in path.read_bytes().splitlines():
            if not valid_uid(value):
                raise BridgeError("known_uid_state_malformed")
            values.add(value)
    return values


def valid_uid(value: bytes) -> bool:
    return bool(value) and len(value) <= MAX_UID_BYTES and all(
        0x21 <= byte <= 0x7E for byte in value
    )


def parse_uidl_response(response: bytes, expected_number: int) -> bytes:
    parts = response.strip().split()
    if len(parts) < 3 or parts[0] != b"+OK" or parts[-2] != str(expected_number).encode():
        raise BridgeError("upstream_uidl_response_malformed")
    uid = parts[-1]
    if not valid_uid(uid):
        raise BridgeError("upstream_uidl_invalid")
    return uid


def parse_list_response(response: bytes, expected_number: int) -> int:
    parts = response.strip().split()
    if (
        len(parts) < 3
        or parts[0] != b"+OK"
        or parts[-2] != str(expected_number).encode()
        or not parts[-1].isdigit()
    ):
        raise BridgeError("upstream_list_response_malformed")
    return int(parts[-1])


def canonical_message(lines: list[bytes], maximum: int) -> bytes:
    content = b"\r\n".join(lines) + b"\r\n"
    if len(content) > maximum:
        raise BridgeError("upstream_message_exceeded_bound")
    return content


def retr_bounded(client, number: int, maximum: int) -> tuple[bytes, list[bytes], int]:
    """Read one POP multiline response incrementally with a retained-byte cap."""
    client._putcmd(f"RETR {number}")
    response = client._getresp()
    lines: list[bytes] = []
    retained = 0
    while True:
        line, _wire_octets = client._getline()
        if line == b".":
            return response, lines, retained
        if line.startswith(b".."):
            line = line[1:]
        next_retained = retained + len(line) + 2
        if next_retained > maximum:
            try:
                client.close()
            finally:
                raise BridgeConnectionAborted("upstream_message_exceeded_bound")
        lines.append(line)
        retained = next_retained


def fetch_batch(
    account: Account,
    username: bytes,
    password: bytes,
    known_uidls: set[bytes],
    *,
    batch_size: int,
    scan_limit: int,
    max_message_bytes: int,
    timeout_seconds: float,
    pop_factory: Callable[..., object] = poplib.POP3_SSL,
    evidence: FetchEvidence | None = None,
) -> tuple[list[Message], FetchEvidence]:
    if username != account.username:
        raise BridgeError("agent_username_mismatch")
    if not 1 <= len(password) <= MAX_CREDENTIAL_BYTES:
        raise BridgeError("agent_password_length_invalid")
    if any(byte < 0x20 or byte > 0x7E for byte in password):
        raise BridgeError("agent_password_not_ascii")

    if evidence is None:
        evidence = FetchEvidence()
    client = None
    authenticated = False
    selected: list[Message] = []
    failure: BridgeError | None = None
    connection_usable = True
    try:
        context = ssl.create_default_context()
        client = pop_factory(
            account.host,
            account.port,
            timeout=timeout_seconds,
            context=context,
        )
        evidence.commands.append("USER")
        client.user(username.decode("ascii"))
        evidence.commands.append("PASS")
        client.pass_(password.decode("ascii"))
        authenticated = True

        evidence.commands.append("STAT")
        count, octets = client.stat()
        evidence.server_message_count = count
        evidence.server_message_octets = octets

        first_number = max(1, count - scan_limit + 1)
        for number in range(count, first_number - 1, -1):
            if len(selected) >= batch_size:
                break
            evidence.commands.append("UIDL")
            uid = parse_uidl_response(client.uidl(number), number)
            evidence.candidates_scanned += 1
            if uid in known_uidls or any(message.uid == uid for message in selected):
                evidence.known_candidates_skipped += 1
                continue

            evidence.commands.append("LIST")
            listed_size = parse_list_response(client.list(number), number)
            if listed_size > max_message_bytes:
                evidence.oversized_candidates_skipped += 1
                continue

            evidence.commands.append("RETR")
            _response, lines, _reported_octets = retr_bounded(
                client,
                number,
                max_message_bytes,
            )
            content = canonical_message(lines, max_message_bytes)
            selected.append(Message(number, uid, content))

        selected.sort(key=lambda message: message.server_number)
        evidence.selected_messages = len(selected)
        evidence.selected_octets = sum(len(message.content) for message in selected)
    except BridgeConnectionAborted as exc:
        connection_usable = False
        failure = exc
    except BridgeError as exc:
        failure = exc
    except (OSError, ssl.SSLError, poplib.error_proto) as exc:
        failure = BridgeError(f"upstream_{type(exc).__name__.lower()}")
    finally:
        if client is not None and connection_usable:
            if authenticated:
                try:
                    evidence.commands.append("RSET")
                    client.rset()
                except Exception as exc:
                    if failure is None:
                        failure = BridgeError("upstream_rset_failed")
            try:
                evidence.commands.append("QUIT")
                client.quit()
            except Exception as exc:
                try:
                    client.close()
                except Exception:
                    pass
                if failure is None:
                    failure = BridgeError("upstream_quit_failed")
    if failure is not None:
        raise failure
    return selected, evidence


def receive_exact(stream: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        chunk = stream.recv(count - len(chunks))
        if not chunk:
            raise BridgeError("bridge_truncated")
        chunks.extend(chunk)
    return bytes(chunks)


def send_blob(stream: socket.socket, value: bytes) -> None:
    stream.sendall(struct.pack("!I", len(value)) + value)


def receive_blob(stream: socket.socket, maximum: int) -> bytes:
    length = struct.unpack("!I", receive_exact(stream, 4))[0]
    if length > maximum:
        raise BridgeError("bridge_blob_too_large")
    return receive_exact(stream, length)


def send_messages(stream: socket.socket, messages: list[Message]) -> None:
    stream.sendall(b"O" + struct.pack("!I", len(messages)))
    for message in messages:
        send_blob(stream, message.uid)
        stream.sendall(struct.pack("!Q", len(message.content)))
        stream.sendall(message.content)


def send_failure(stream: socket.socket, category: str) -> None:
    safe = re.sub(r"[^a-z0-9_.-]", "_", category.lower()).encode("ascii")[:128]
    stream.sendall(b"E")
    send_blob(stream, safe)


def receive_messages(stream: socket.socket, max_message_bytes: int) -> list[Message]:
    status = receive_exact(stream, 1)
    if status == b"E":
        category = receive_blob(stream, 128).decode("ascii", errors="replace")
        raise BridgeError(category)
    if status != b"O":
        raise BridgeError("bridge_status_invalid")
    count = struct.unpack("!I", receive_exact(stream, 4))[0]
    if count > 100:
        raise BridgeError("bridge_message_count_invalid")
    messages: list[Message] = []
    for number in range(1, count + 1):
        uid = receive_blob(stream, MAX_UID_BYTES)
        if not valid_uid(uid):
            raise BridgeError("bridge_uid_invalid")
        length = struct.unpack("!Q", receive_exact(stream, 8))[0]
        if length > max_message_bytes:
            raise BridgeError("bridge_message_too_large")
        content = receive_exact(stream, length)
        messages.append(Message(number, uid, content))
    return messages


def atomic_write(path: Path, text: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode)


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


def evidence_text(
    status: str,
    category: str,
    evidence: FetchEvidence,
    started: float,
) -> str:
    commands = ",".join(evidence.commands)
    lines = [
        f"status={status}",
        f"failure_category={category if category else 'none'}",
        "tls_certificate_verification=system_default",
        "credential_storage=memory-only",
        "delete_command_available=no",
        f"commands_sent={commands}",
        f"server_message_count={evidence.server_message_count}",
        f"server_message_octets={evidence.server_message_octets}",
        f"candidates_scanned={evidence.candidates_scanned}",
        f"known_candidates_skipped={evidence.known_candidates_skipped}",
        f"oversized_candidates_skipped={evidence.oversized_candidates_skipped}",
        f"selected_messages={evidence.selected_messages}",
        f"selected_octets={evidence.selected_octets}",
        f"elapsed_seconds={time.monotonic() - started:.3f}",
        f"finished_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
    ]
    return "\n".join(lines) + "\n"


def upstream_main(args: argparse.Namespace) -> int:
    started = time.monotonic()
    evidence = FetchEvidence()
    category = ""
    socket_path: Path = args.socket
    try:
        account = parse_account(
            args.agent_ini,
            args.account_uid,
            args.expected_host,
            args.expected_port,
        )
        known = read_known_uidls(args.known_uid_file, args.delivered_uid_file)
        if socket_path.exists() or socket_path.is_symlink():
            raise BridgeError("bridge_socket_already_exists")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.settimeout(args.accept_timeout)
        server.bind(str(socket_path))
        os.chmod(socket_path, 0o600)
        server.listen(1)
        atomic_write(args.ready_file, "ready=yes\n")
        with server:
            connection, _address = server.accept()
            with connection:
                connection.settimeout(args.request_timeout)
                if receive_exact(connection, len(PROTOCOL_MAGIC)) != PROTOCOL_MAGIC:
                    raise BridgeError("bridge_magic_invalid")
                username = receive_blob(connection, MAX_CREDENTIAL_BYTES)
                password = receive_blob(connection, MAX_CREDENTIAL_BYTES)
                try:
                    messages, evidence = fetch_batch(
                        account,
                        username,
                        password,
                        known,
                        batch_size=args.batch_size,
                        scan_limit=args.scan_limit,
                        max_message_bytes=args.max_message_bytes,
                        timeout_seconds=args.upstream_timeout,
                        evidence=evidence,
                    )
                    selected_uid_data = b"".join(message.uid + b"\n" for message in messages)
                    atomic_write_bytes(args.selected_uid_file, selected_uid_data)
                    send_messages(connection, messages)
                except BridgeError as exc:
                    category = exc.category
                    send_failure(connection, category)
                    raise
        atomic_write(args.result, evidence_text("complete", "", evidence, started))
        return 0
    except BridgeError as exc:
        category = exc.category
        atomic_write(args.result, evidence_text("failed", category, evidence, started))
        return 1
    except (OSError, ValueError) as exc:
        category = f"local_{type(exc).__name__.lower()}"
        atomic_write(args.result, evidence_text("failed", category, evidence, started))
        return 1
    finally:
        try:
            if socket_path.exists() or socket_path.is_symlink():
                socket_path.unlink()
        except OSError:
            pass


class AggregateLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.counts: collections.Counter[str] = collections.Counter()
        self.connections = 0
        self.messages_loaded = 0
        self.message_octets_loaded = 0
        self.content_responses = 0
        self.destructive_rejections = 0
        self.bridge_failures = 0

    def write(self, status: str) -> None:
        lines = [
            f"status={status}",
            f"updated_utc={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
            f"connections={self.connections}",
            f"messages_loaded={self.messages_loaded}",
            f"message_octets_loaded={self.message_octets_loaded}",
            f"content_responses_served={self.content_responses}",
            f"destructive_commands_rejected={self.destructive_rejections}",
            f"bridge_failures={self.bridge_failures}",
        ]
        for command in sorted(self.counts):
            lines.append(f"command_{command}={self.counts[command]}")
        atomic_write(self.path, "\n".join(lines) + "\n")


def send_line(stream, value: bytes) -> None:
    stream.write(value + b"\r\n")


def send_multiline(stream, lines) -> None:
    for line in lines:
        if line.startswith(b"."):
            line = b"." + line
        send_line(stream, line)
    send_line(stream, b".")
    stream.flush()


def content_lines(content: bytes) -> list[bytes]:
    if not content.endswith(b"\r\n"):
        raise BridgeError("local_message_not_canonical")
    return content[:-2].split(b"\r\n")


def parse_index(argument: bytes, count: int) -> int | None:
    fields = argument.split()
    if len(fields) != 1 or not fields[0].isdigit():
        return None
    index = int(fields[0])
    return index if 1 <= index <= count else None


def load_upstream_batch(
    socket_path: Path,
    username: bytes,
    password: bytes,
    max_message_bytes: int,
) -> list[Message]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(600)
    with connection:
        connection.connect(str(socket_path))
        connection.sendall(PROTOCOL_MAGIC)
        send_blob(connection, username)
        send_blob(connection, password)
        return receive_messages(connection, max_message_bytes)


def handle_pop_connection(
    connection: socket.socket,
    args: argparse.Namespace,
    aggregate: AggregateLog,
    state: dict[str, object],
) -> None:
    aggregate.connections += 1
    aggregate.write("serving")
    connection.settimeout(300)
    with connection:
        stream = connection.makefile("rwb", buffering=64 * 1024)
        authenticated = False
        current_user = b""
        content_message_index: int | None = None
        retr_served = False
        top_served = False
        send_line(stream, b"+OK bounded Forte Agent POP bridge")
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
            aggregate.write("serving")

            if command == "CAPA":
                send_line(stream, b"+OK capability list follows")
                send_multiline(stream, [b"USER", b"UIDL", b"TOP", b"IMPLEMENTATION bounded-bridge"])
            elif command == "USER":
                if not 1 <= len(argument) <= MAX_CREDENTIAL_BYTES:
                    send_line(stream, b"-ERR invalid USER")
                else:
                    current_user = argument
                    send_line(stream, b"+OK user accepted")
            elif command == "PASS":
                if not current_user or not 1 <= len(argument) <= MAX_CREDENTIAL_BYTES:
                    send_line(stream, b"-ERR USER required before PASS")
                else:
                    try:
                        if state.get("messages") is None:
                            messages = load_upstream_batch(
                                args.socket,
                                current_user,
                                argument,
                                args.max_message_bytes,
                            )
                            state["messages"] = messages
                            state["credential_digest"] = hashlib.sha256(
                                current_user + b"\0" + argument
                            ).digest()
                            aggregate.messages_loaded = len(messages)
                            aggregate.message_octets_loaded = sum(
                                len(message.content) for message in messages
                            )
                            aggregate.write("serving")
                        else:
                            digest = hashlib.sha256(current_user + b"\0" + argument).digest()
                            if digest != state.get("credential_digest"):
                                raise BridgeError("repeat_credential_mismatch")
                        authenticated = True
                        send_line(stream, b"+OK bounded local maildrop ready")
                    except (BridgeError, OSError):
                        aggregate.bridge_failures += 1
                        aggregate.write("serving")
                        send_line(stream, b"-ERR upstream bridge failed safely")
            elif command == "QUIT":
                send_line(stream, b"+OK bounded bridge closing")
                stream.flush()
                break
            elif not authenticated:
                send_line(stream, b"-ERR authenticate first")
            else:
                messages = state.get("messages")
                if not isinstance(messages, list):
                    raise BridgeError("local_message_state_invalid")
                if command == "STAT":
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
                        send_multiline(
                            stream,
                            [
                                f"{index} {len(message.content)}".encode("ascii")
                                for index, message in enumerate(messages, start=1)
                            ],
                        )
                elif command == "UIDL":
                    if argument:
                        index = parse_index(argument, len(messages))
                        if index is None:
                            send_line(stream, b"-ERR no such message")
                        else:
                            send_line(stream, b"+OK " + str(index).encode() + b" " + messages[index - 1].uid)
                    else:
                        send_line(stream, b"+OK unique-id listing follows")
                        send_multiline(
                            stream,
                            [
                                str(index).encode() + b" " + message.uid
                                for index, message in enumerate(messages, start=1)
                            ],
                        )
                elif command == "RETR":
                    index = parse_index(argument, len(messages))
                    if index is None:
                        send_line(stream, b"-ERR no such message")
                    elif retr_served:
                        send_line(stream, b"-ERR RETR content limit reached")
                    elif content_message_index is not None and content_message_index != index:
                        send_line(stream, b"-ERR bounded content message changed")
                    else:
                        message = messages[index - 1]
                        retr_served = True
                        content_message_index = index
                        aggregate.content_responses += 1
                        aggregate.write("serving")
                        send_line(stream, f"+OK {len(message.content)} octets".encode("ascii"))
                        send_multiline(stream, content_lines(message.content))
                elif command == "TOP":
                    fields = argument.split()
                    if len(fields) != 2 or not fields[0].isdigit() or not fields[1].isdigit():
                        send_line(stream, b"-ERR invalid TOP")
                    else:
                        index = int(fields[0])
                        body_count = int(fields[1])
                        if not 1 <= index <= len(messages):
                            send_line(stream, b"-ERR no such message")
                        elif top_served:
                            send_line(stream, b"-ERR TOP content limit reached")
                        elif content_message_index is not None and content_message_index != index:
                            send_line(stream, b"-ERR bounded content message changed")
                        else:
                            lines = content_lines(messages[index - 1].content)
                            separator = lines.index(b"") if b"" in lines else len(lines)
                            subset = lines[: separator + 1] + lines[separator + 1 : separator + 1 + body_count]
                            top_served = True
                            content_message_index = index
                            aggregate.content_responses += 1
                            aggregate.write("serving")
                            send_line(stream, b"+OK top of message follows")
                            send_multiline(stream, subset)
                elif command == "DELE":
                    aggregate.destructive_rejections += 1
                    aggregate.write("serving")
                    send_line(stream, b"-ERR DELE disabled by bounded bridge")
                elif command in {"NOOP", "RSET"}:
                    send_line(stream, b"+OK")
                elif command == "LAST":
                    send_line(stream, b"+OK 0")
                else:
                    send_line(stream, b"-ERR unsupported command")
            stream.flush()
    aggregate.write("listening")


def listener_main(args: argparse.Namespace) -> int:
    aggregate = AggregateLog(args.aggregate_log)
    state: dict[str, object] = {"messages": None, "credential_digest": None}
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.listen_host, args.port))
    server.listen(4)
    server.settimeout(1.0)
    atomic_write(args.ready_file, "ready=yes\n")
    aggregate.write("listening")
    with server:
        while not stopping and aggregate.connections < MAX_LOCAL_CONNECTIONS:
            try:
                connection, _address = server.accept()
            except socket.timeout:
                continue
            try:
                handle_pop_connection(connection, args, aggregate, state)
            except (BridgeError, OSError):
                aggregate.bridge_failures += 1
                aggregate.write("listening")
    aggregate.write("stopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="mode", required=True)

    upstream = subparsers.add_parser("upstream")
    upstream.add_argument("--socket", required=True, type=Path)
    upstream.add_argument("--ready-file", required=True, type=Path)
    upstream.add_argument("--result", required=True, type=Path)
    upstream.add_argument("--selected-uid-file", required=True, type=Path)
    upstream.add_argument("--agent-ini", required=True, type=Path)
    upstream.add_argument("--known-uid-file", required=True, type=Path)
    upstream.add_argument("--delivered-uid-file", required=True, type=Path)
    upstream.add_argument("--account-uid", required=True, type=int)
    upstream.add_argument("--expected-host", required=True)
    upstream.add_argument("--expected-port", required=True, type=int)
    upstream.add_argument("--batch-size", type=int, default=1)
    upstream.add_argument("--scan-limit", type=int, default=500)
    upstream.add_argument("--max-message-bytes", type=int, default=10 * 1024 * 1024)
    upstream.add_argument("--upstream-timeout", type=float, default=180)
    upstream.add_argument("--accept-timeout", type=float, default=3600)
    upstream.add_argument("--request-timeout", type=float, default=900)

    listener = subparsers.add_parser("listener")
    listener.add_argument("--socket", required=True, type=Path)
    listener.add_argument("--ready-file", required=True, type=Path)
    listener.add_argument("--aggregate-log", required=True, type=Path)
    listener.add_argument("--listen-host", default="127.0.0.1")
    listener.add_argument("--port", type=int, default=8110)
    listener.add_argument("--max-message-bytes", type=int, default=10 * 1024 * 1024)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    validate_unix_socket_path(args.socket)
    if args.mode == "upstream":
        if not 1 <= args.expected_port <= 65535:
            raise BridgeError("expected_port_invalid")
        if not 1 <= args.batch_size <= 10:
            raise BridgeError("batch_size_invalid")
        if not 1 <= args.scan_limit <= 5000:
            raise BridgeError("scan_limit_invalid")
        if not 1024 <= args.max_message_bytes <= 100 * 1024 * 1024:
            raise BridgeError("message_bound_invalid")
    else:
        if args.listen_host != "127.0.0.1":
            raise BridgeError("listener_must_be_loopback")
        if not 1024 <= args.port <= 65535:
            raise BridgeError("listener_port_invalid")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_args(args)
        return upstream_main(args) if args.mode == "upstream" else listener_main(args)
    except BridgeError as exc:
        print(f"safe_failure={exc.category}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit('INTERNAL MODULE: use the portable debian_launcher.py entrypoint.')
