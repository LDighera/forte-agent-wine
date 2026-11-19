#!/usr/bin/env python3
"""Classify full-inventory/lazy-body sessions; never read content or UID values."""
import argparse
import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('full_mail_evidence_base', Path(__file__).with_name('classify-pop-nominal-session.py'))
C = importlib.util.module_from_spec(spec); spec.loader.exec_module(C)


def need(ok, category):
    if not ok: raise C.ClassificationError(category)


def classify_account(runtime, uid):
    b = C.values(runtime/f'uid-{uid}-broker.txt')
    l = C.values(runtime/f'uid-{uid}-listener.txt')
    for data in (b, l):
        need(data.get('status') == 'stopped', 'component_not_stopped')
        need(data.get('uid_values_logged') == data.get('message_content_logged') == 'no', 'privacy_invalid')
        need(C.integer(data, 'transactions_failed', 10) == 0, 'failed_transaction')
    need(b.get('delete_command_available') == 'no', 'no_delete_invalid')
    need(l.get('command_DELE', '0') == '0' and
         C.integer(l, 'destructive_commands_rejected', 1000) == 0, 'delete_attempt')
    n = C.integer(b, 'connection_attempts', 10)
    need(C.integer(b, 'transactions_completed', 10) == C.integer(l, 'connections', 10) ==
         C.integer(l, 'transactions_completed', 10) == n, 'transaction_count_mismatch')
    selected = C.integer(b, 'messages_selected', 10000)
    committed = C.integer(b, 'messages_committed', selected)
    responses = C.integer(l, 'content_responses_served', selected*2)
    need(C.integer(l, 'messages_loaded', 10000) == selected and
         C.integer(l, 'messages_committed', selected) == committed and responses >= committed,
         'delivery_count_mismatch')
    need(C.integer(b, 'retrieval_candidates_deferred', 10000) == 0 and
         C.integer(b, 'retr_error_closed_connections', 10) == 0, 'provider_failure_present')
    directory = runtime/f'uid-{uid}-transactions'
    need(directory.is_dir() and not directory.is_symlink(), 'evidence_directory_invalid')
    files = sorted(directory.glob('transaction-*-upstream.txt'))
    need(len(files) == n, 'evidence_count_mismatch')
    total_selected = total_committed = total_octets = remaining = 0
    for i, file in enumerate(files, 1):
        need(file.name == f'transaction-{i:04d}-upstream.txt', 'evidence_sequence_invalid')
        t = C.values(file)
        need(t.get('transaction') == str(i) and t.get('status') == 'complete' and
             t.get('failure_category') == 'none' and t.get('protocol') == 'full-mail-v1' and
             t.get('ledger_published') == t.get('ledger_durable') == 'yes' and
             t.get('inventory_complete') == 'yes' and t.get('provider_cleanup') == 'rset-quit',
             'transaction_contract_invalid')
        need(t.get('delete_command_available') == t.get('uid_values_logged') ==
             t.get('message_content_logged') == 'no', 'transaction_privacy_or_delete_invalid')
        offered = C.integer(t, 'selected_messages', 1000)
        fetched = C.integer(t, 'fetched_messages', offered)
        delivered = C.integer(t, 'committed_messages', fetched)
        octets = C.integer(t, 'selected_octets', 256*1024*1024)
        need(t.get('commands_sent', '').split(',') ==
             ['USER', 'PASS', 'STAT', 'UIDL', 'LIST', 'STAT'] + ['UIDL', 'RETR']*fetched + ['RSET', 'QUIT'],
             'upstream_command_sequence_invalid')
        server = C.integer(t, 'server_messages', 100000)
        unknown = C.integer(t, 'untracked_messages', server)
        oversized = C.integer(t, 'oversized_messages', unknown)
        deferred = C.integer(t, 'deferred_messages', unknown)
        paused = C.integer(t, 'resource_paused_messages', unknown)
        remaining = C.integer(t, 'remaining_untracked_messages', unknown)
        need(offered == C.integer(t, 'offered_messages', 1000) and
             offered + oversized + deferred + paused == unknown and
             remaining == unknown-delivered, 'remaining_count_invalid')
        C.integer(t, 'offered_octets', 256*1024*1024)
        C.integer(t, 'retrieval_candidates_deferred', 0)
        inventory = C.values(directory/f'transaction-{i:04d}-inventory.txt')
        need(all(t.get(k) == inventory.get(k) for k in ('server_messages', 'untracked_messages',
             'oversized_messages', 'deferred_messages', 'offered_messages', 'offered_octets',
             'resource_paused_messages')), 'inventory_evidence_mismatch')
        total_selected += offered; total_committed += delivered; total_octets += octets
    need((total_selected, total_committed, total_octets) ==
         (selected, committed, C.integer(b, 'selected_octets', 10*256*1024*1024)),
         'aggregate_mismatch')
    return dict(connections=n, selected=selected, committed=committed, responses=responses,
                deferred=0, remaining=remaining)


