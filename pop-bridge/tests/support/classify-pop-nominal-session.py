#!/usr/bin/env python3
"""Classify four-account persistent-session evidence without private values."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path


MAX_CONNECTIONS_PER_ACCOUNT = 10
MAX_MESSAGES_PER_TRANSACTION = 10
MAX_MESSAGES_TOTAL = 400
MAX_DEFERRED_PER_TRANSACTION = 1000
COMMAND_COUNTER_KEYS = frozenset(
    f"command_{command}"
    for command in ("CAPA", "USER", "PASS", "QUIT", "STAT", "LIST", "UIDL",
                    "RETR", "TOP", "DELE", "NOOP", "RSET", "LAST")
)


class ClassificationError(RuntimeError):
    pass


def load_multi(path: Path):
    spec = importlib.util.spec_from_file_location("nominal_classifier_multi", path)
    if spec is None or spec.loader is None:
        raise ClassificationError("multi_import_invalid")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def values(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        raise ClassificationError("evidence_file_invalid")
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ClassificationError("evidence_unreadable") from exc
    for line in lines:
        if "=" not in line:
            raise ClassificationError("evidence_line_invalid")
        key, value = line.split("=", 1)
        if (
            (not re.fullmatch(r"[a-z][a-z0-9_]*", key) and key not in COMMAND_COUNTER_KEYS)
            or key in result
        ):
            raise ClassificationError("evidence_key_invalid")
        if key.startswith("command_") and (
            key not in COMMAND_COUNTER_KEYS or not re.fullmatch(r"0|[1-9][0-9]*", value)
        ):
            raise ClassificationError("command_counter_invalid")
        result[key] = value
    return result


def integer(data: dict[str, str], key: str, maximum: int) -> int:
    raw = data.get(key, "")
    if not raw.isdigit() or str(int(raw)) != raw:
        raise ClassificationError(f"{key}_invalid")
    result = int(raw)
    if not 0 <= result <= maximum:
        raise ClassificationError(f"{key}_out_of_bounds")
    return result


def classify_account(
    runtime: Path, uid: int, required_connections: int | None = None, batch_size: int = 10
) -> dict[str, int]:
    if type(batch_size) is not int or batch_size not in (10,25,50):
        raise ClassificationError("batch_bound_invalid")
    broker = values(runtime / f"uid-{uid}-broker.txt")
    listener = values(runtime / f"uid-{uid}-listener.txt")
    if broker.get("status") != "stopped" or listener.get("status") != "stopped":
        raise ClassificationError("component_not_stopped")
    if broker.get("delete_command_available") != "no":
        raise ClassificationError("upstream_delete_boundary_invalid")
    if broker.get("uid_values_logged") != "no" or broker.get("message_content_logged") != "no":
        raise ClassificationError("upstream_privacy_boundary_invalid")
    if listener.get("uid_values_logged") != "no" or listener.get("message_content_logged") != "no":
        raise ClassificationError("listener_privacy_boundary_invalid")
    if listener.get("command_DELE", "0") != "0":
        raise ClassificationError("failed_or_destructive_transaction")

    attempts = integer(broker, "connection_attempts", MAX_CONNECTIONS_PER_ACCOUNT)
    if required_connections is not None and attempts != required_connections:
        raise ClassificationError("required_connection_evidence_mismatch")
    broker_complete = integer(broker, "transactions_completed", MAX_CONNECTIONS_PER_ACCOUNT)
    broker_failed = integer(broker, "transactions_failed", MAX_CONNECTIONS_PER_ACCOUNT)
    listener_connections = integer(listener, "connections", MAX_CONNECTIONS_PER_ACCOUNT)
    listener_complete = integer(listener, "transactions_completed", MAX_CONNECTIONS_PER_ACCOUNT)
    listener_failed = integer(listener, "transactions_failed", MAX_CONNECTIONS_PER_ACCOUNT)
    selected = integer(
        broker,
        "messages_selected",
        MAX_CONNECTIONS_PER_ACCOUNT * MAX_MESSAGES_PER_TRANSACTION,
    )
    broker_deferred = integer(
        broker,
        "retrieval_candidates_deferred",
        MAX_CONNECTIONS_PER_ACCOUNT * MAX_DEFERRED_PER_TRANSACTION,
    )
    retr_error_closed = integer(
        broker,
        "retr_error_closed_connections",
        MAX_CONNECTIONS_PER_ACCOUNT,
    )
    broker_committed = integer(broker, "messages_committed", selected)
    listener_committed = integer(listener, "messages_committed", selected)
    loaded = integer(listener, "messages_loaded", selected)
    responses = integer(listener, "content_responses_served", selected * 2)
    destructive = integer(listener, "destructive_commands_rejected", 1000)
    if any((broker_failed, listener_failed, destructive)):
        raise ClassificationError("failed_or_destructive_transaction")
    if not (
        attempts == broker_complete == listener_connections == listener_complete
        and broker_committed == listener_committed
        and selected == loaded
        and broker_committed <= responses <= selected * 2
    ):
        raise ClassificationError("cross_component_count_mismatch")

    evidence_dir = runtime / f"uid-{uid}-transactions"
    if not evidence_dir.is_dir() or evidence_dir.is_symlink():
        raise ClassificationError("transaction_directory_invalid")
    files = sorted(evidence_dir.glob("transaction-*-upstream.txt"))
    if len(files) != broker_complete:
        raise ClassificationError("transaction_evidence_count_mismatch")
    evidence_selected = 0
    evidence_deferred = 0
    evidence_retr_error_closed = 0
    for number, path in enumerate(files, start=1):
        if path.name != f"transaction-{number:04d}-upstream.txt":
            raise ClassificationError("transaction_sequence_invalid")
        transaction = values(path)
        if transaction.get("transaction") != str(number) or transaction.get("status") != "complete":
            raise ClassificationError("transaction_status_invalid")
        if transaction.get("failure_category") != "none":
            raise ClassificationError("transaction_failure_present")
        if transaction.get("delete_command_available") != "no":
            raise ClassificationError("transaction_delete_boundary_invalid")
        if (
            transaction.get("uid_values_logged") != "no"
            or transaction.get("message_content_logged") != "no"
        ):
            raise ClassificationError("transaction_privacy_boundary_invalid")
        commands = transaction.get("commands_sent", "").split(",")
        if "DELE" in commands or not commands:
            raise ClassificationError("transaction_command_contract_invalid")
        transaction_selected = integer(transaction, "selected_messages", batch_size)
        transaction_deferred = integer(
            transaction,
            "retrieval_candidates_deferred",
            MAX_DEFERRED_PER_TRANSACTION,
        )
        if commands.count("RETR") != transaction_selected + transaction_deferred:
            raise ClassificationError("transaction_retr_count_invalid")
        cleanup = transaction.get("provider_cleanup")
        if cleanup == "rset-quit":
            if transaction_deferred != 0:
                raise ClassificationError("transaction_deferred_cleanup_invalid")
            if commands[-2:] != ["RSET", "QUIT"]:
                raise ClassificationError("transaction_command_contract_invalid")
        elif cleanup == "closed-after-retr-error":
            if transaction_deferred != 1 or commands[-1] != "RETR" or "RSET" in commands or "QUIT" in commands:
                raise ClassificationError("transaction_deferred_cleanup_invalid")
            evidence_retr_error_closed += 1
        elif cleanup == "closed-after-uidl-eof":
            # This is a partial upstream batch, never a successful RSET/QUIT or
            # an invented/deferred UID. Only completed local acknowledgements
            # may establish delivery; the failed UIDL position must be revisited.
            scanned = integer(transaction, "candidates_scanned", 1000)
            known = integer(transaction, "known_candidates_skipped", scanned)
            oversized = integer(transaction, "oversized_candidates_skipped", scanned)
            if not (
                transaction_selected > 0 and transaction_deferred == 0
                and transaction.get("scan_stop") == "uidl-eof-partial"
                and transaction.get("upstream_issue_phase") == "uidl"
                and transaction.get("upstream_issue_kind") == "connection_eof"
                and transaction.get("upstream_login_completed") == "yes"
                and commands[:3] == ["USER", "PASS", "STAT"]
                and commands[-1] == "UIDL"
                and set(commands[3:]) <= {"UIDL", "LIST", "RETR"}
                and commands.count("UIDL") == scanned + 1
                and commands.count("LIST") == scanned - known
                and scanned == known + oversized + transaction_selected
            ):
                raise ClassificationError("transaction_uidl_eof_partial_invalid")
            # Every completed candidate is UIDL, UIDL/LIST, or UIDL/LIST/RETR;
            # the final bare UIDL is the failed candidate, not scanned progress.
            groups = []
            for command in commands[3:-1]:
                if command == "UIDL":
                    groups.append([])
                elif not groups:
                    raise ClassificationError("transaction_uidl_eof_sequence_invalid")
                else:
                    groups[-1].append(command)
            if (len(groups) != scanned or any(group not in
                    ([], ["LIST"], ["LIST", "RETR"]) for group in groups)):
                raise ClassificationError("transaction_uidl_eof_sequence_invalid")
        else:
            raise ClassificationError("transaction_cleanup_invalid")
        evidence_selected += transaction_selected
        evidence_deferred += transaction_deferred
    if evidence_selected != selected:
        raise ClassificationError("selected_message_aggregate_mismatch")
    if evidence_deferred != broker_deferred:
        raise ClassificationError("deferred_message_aggregate_mismatch")
    if evidence_retr_error_closed != retr_error_closed:
        raise ClassificationError("retr_error_cleanup_aggregate_mismatch")
    return {
        "connections": attempts,
        "selected": selected,
        "committed": broker_committed,
        "responses": responses,
        "deferred": broker_deferred,
    }


