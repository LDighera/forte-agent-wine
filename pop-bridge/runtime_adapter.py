"""Single-account transaction adapter; OFFLINE development API, no live launcher.

An explicit fake provider factory and already-connected local socket are supplied
by tests. This module binds no listener, contacts no provider on import, and never
parses AGENT.INI. Core transaction code remains byte-identical to production.
"""
from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace

import account_state

CORE = Path(__file__).resolve().parent/'core'
spec = importlib.util.spec_from_file_location('portable_adapter_full', CORE/'pop-full-mail-bridge.py')
FULL = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = FULL
spec.loader.exec_module(FULL)
BASE = FULL.C.load_base_bridge(CORE/'pop-bounded-bridge.py')


class Runtime:
    def __init__(self, account, state, evidence):
        self.account = account.as_bridge_account(BASE)
        self.state = state
        self.args = SimpleNamespace(
            request_timeout=300, upstream_timeout=180,
            known_uid_file=state.known, delivered_ledger=state.delivered,
            deferred_ledger=state.deferred, evidence_dir=evidence,
        )
        self.count = 0
        self._transaction_lock = threading.Lock()

    def transaction(self, connection, aggregate, *, factory):
        account_state.need(self._transaction_lock.acquire(blocking=False),
                           'transaction_in_progress')
        try:
            account_state.need(self.count < 10, 'session_transaction_limit')
            self.count += 1
            return FULL.broker_transaction(BASE, connection, self.args, self.account,
                                           self.count, aggregate, factory=factory)
        finally:
            self._transaction_lock.release()


@contextmanager
def session(config, account):
    with account_state.locked(config, account) as state:
        # The core's provider-stop marker is evidence_dir.parent, so account-wide.
        # New evidence names avoid overwriting earlier transactions on reopening.
        evidence = Path(tempfile.mkdtemp(prefix='transactions-', dir=state.path))
        runtime = Runtime(account, state, evidence)
        try:
            yield runtime
        finally:
            # Do not release the account lock while a transaction is in flight.
            with runtime._transaction_lock:
                runtime.count = 10  # Retained objects cannot work after unlock.
