#!/usr/bin/env python3
"""Provider-free tests of the actual full-mail broker/listener implementation."""
import importlib.util
import io
from pathlib import Path
import poplib
import socket
import ssl
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W))
import bridge_config

def forbid_internet(event, args):
    if event == 'socket.getaddrinfo' or (event in ('socket.connect', 'socket.bind') and
                                       isinstance(args[1], tuple)):
        raise RuntimeError('Internet sockets forbidden in offline tests')

sys.addaudithook(forbid_internet)
spec = importlib.util.spec_from_file_location('full_mail_tested', W/'core/pop-full-mail-bridge.py')
F = importlib.util.module_from_spec(spec); sys.modules[spec.name] = F; spec.loader.exec_module(F)
B = F.C.load_base_bridge(W/'core/pop-bounded-bridge.py')
cspec = importlib.util.spec_from_file_location('full_mail_classifier_tested', W/'tests/support/classify-full-mail-session.py')
CLASS = importlib.util.module_from_spec(cspec); cspec.loader.exec_module(CLASS)
BODY = b'Date: Mon, 14 Sep 2026 12:00:00 -0700\r\nSubject: synthetic\r\n\r\n.dot\r\n'


class FakePOP:
    def __init__(self, count=31, bad_identity=False, refuse=False):
        self.count = count
        self.commands = []
        self.bad_identity = bad_identity
        self.refuse = refuse
        self.pending = []
        self.closed = False
        self.file = None

    def factory(self, host, port, *, context, timeout):
        assert host == 'controlled.invalid' and port == 995
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        return self

    def user(self, value): self.commands.append('USER')
    def pass_(self, value): self.commands.append('PASS')
    def stat(self):
        self.commands.append('STAT'); return self.count, self.count * len(BODY)
    def uidl(self, number=None):
        self.commands.append('UIDL')
        if number is not None:
            return b'+OK %d u%d' % (number, number + (1 if self.bad_identity else 0))
        return b'+OK', [b'%d u%d' % (i, i) for i in range(1, self.count+1)], 0
    def list(self):
        self.commands.append('LIST')
        return b'+OK', [b'%d %d' % (i, len(BODY)) for i in range(1, self.count+1)], 0
    def _putcmd(self, command):
        assert command.startswith('RETR ')
        self.commands.append('RETR')
        self.pending = [line if not line.startswith(b'.') else b'.'+line for line in BODY[:-2].split(b'\r\n')] + [b'.']
        self.file = io.BytesIO(b'\r\n'.join(self.pending)+b'\r\n')
    def _getresp(self):
        if self.refuse: raise poplib.error_proto(b'-ERR PRIVATE SERVER TEXT')
        return b'+OK'
    def _getline(self):
        line = self.pending.pop(0); return line, len(line)+2
    def rset(self): self.commands.append('RSET')
    def quit(self): self.commands.append('QUIT'); self.closed = True
    def close(self): self.closed = True


class Cycle:
    def __init__(self, fake, known=b'', deferred=b'', ack_loss=False, account=None, runtime=None):
        self.fake = fake
        self.temp = tempfile.TemporaryDirectory(prefix='t-', dir=W/'records')
        self.root = Path(self.temp.name)
        for name, data in (('known', known), ('delivered', b''), ('deferred', deferred)):
            (self.root/name).write_bytes(data)
        (self.root/'evidence').mkdir()
        self.args = types.SimpleNamespace(request_timeout=5, upstream_timeout=5,
            known_uid_file=self.root/'known', delivered_ledger=self.root/'delivered',
            deferred_ledger=self.root/'deferred', evidence_dir=self.root/'evidence',
            aggregate_log=self.root/'listener.txt', client_timeout=5,
            max_message_bytes=F.MAX_MESSAGE, socket=self.root/'mail.sock')
        self.broker = F.C.BrokerAggregate(attempts=1)
        self.listener = F.C.ListenerAggregate()
        self.errors = []
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.args.socket)); self.server.listen(1)
        self.client_socket, local = socket.socketpair()
        self.client_socket.settimeout(8)
        self.stream = self.client_socket.makefile('rwb', buffering=0)
        def broker():
            try:
                peer, _ = self.server.accept()
                with peer:
                    class Connection:
                        def __getattr__(self, name): return getattr(peer, name)
                        def sendall(self, data):
                            if ack_loss and data == F.C.COMMIT_OK: raise BrokenPipeError('synthetic ack loss')
                            return peer.sendall(data)
                    if runtime is not None:
                        runtime.transaction(Connection(), self.broker, factory=fake.factory)
                    else:
                        F.broker_transaction(B, Connection(), self.args,
                            account or types.SimpleNamespace(username=b'user', host='controlled.invalid', port=995),
                            1, self.broker, factory=fake.factory)
            except Exception as exc: self.errors.append(exc)
        def listener():
            try:
                F.handle_agent_connection(B, local, self.args, self.listener)
            except Exception as exc: self.errors.append(exc)
        self.threads = [threading.Thread(target=broker), threading.Thread(target=listener)]
        for t in self.threads: t.start()
        assert self.stream.readline().startswith(b'+OK')

    def command(self, line):
        self.stream.write(line+b'\r\n'); return self.stream.readline()

    def login(self):
        assert self.command(b'USER user').startswith(b'+OK')
        assert self.command(b'PASS secret').startswith(b'+OK')

    def retrieve(self, i, top=False):
        answer = self.command((b'TOP %d 0' if top else b'RETR %d') % i)
        assert answer.startswith(b'+OK'), answer
        body = []
        while True:
            line = self.stream.readline()
            if line == b'.\r\n': return b''.join(body)
            assert line, 'truncated local content'
            body.append(line[1:] if line.startswith(b'..') else line)

    def finish(self, quit=True):
        if quit:
            assert self.command(b'QUIT').startswith(b'+OK')
            # QUIT success is not sent before the ledger is durable.
            assert self.broker.completed == 1
        self.stream.close(); self.client_socket.close()
        for t in self.threads:
            t.join(10); assert not t.is_alive(), 'test peer stuck'
        self.server.close()

    def cleanup(self): self.temp.cleanup()

    def classify(self):
        (self.root/'uid-1-broker.txt').write_text(self.broker.text('stopped'))
        (self.root/'uid-1-listener.txt').write_text(self.listener.text('stopped'))
        if (self.root/'evidence').exists():
            (self.root/'evidence').rename(self.root/'uid-1-transactions')
        return CLASS.classify_account(self.root, 1)


class FullMailTests(unittest.TestCase):
    def test_harness_blocks_dns_and_internet_sockets(self):
        with self.assertRaisesRegex(RuntimeError, 'Internet sockets forbidden'):
            socket.getaddrinfo('example.invalid', 995)
        with socket.socket(socket.AF_INET) as attempted:
            with self.assertRaisesRegex(RuntimeError, 'Internet sockets forbidden'):
                attempted.connect(('127.0.0.1', 9))

    def test_configured_account_reaches_unchanged_transaction(self):
        import json
        config = bridge_config.parse(json.dumps({
            'version': 1, 'state_root': '/offline/state', 'accounts': [{
                'name': 'reader', 'agent_uid': 41, 'host': 'controlled.invalid',
                'port': 995, 'username': 'user', 'listen_port': 21110,
                'known_uids_file': '/offline/known41.dat'
            }]}))
        c = Cycle(FakePOP(1), account=config.accounts[0].as_bridge_account(B))
        try:
            c.login(); self.assertEqual(c.retrieve(1), BODY); c.finish()
            self.assertEqual((c.root/'delivered').read_bytes(), b'u1\n')
            self.assertNotIn('DELE', c.fake.commands)
            self.assertFalse(c.errors)
        finally: c.cleanup()

    def test_all_31_lazy_bodies_dot_stuffing_quit_and_repeat_dedup(self):
        c = Cycle(FakePOP())
        try:
            c.login()
            self.assertNotIn('RETR', c.fake.commands)
            self.assertEqual(c.command(b'STAT'), b'+OK 31 %d\r\n' % (31*len(BODY)))
            c.retrieve(1, top=True)
            for i in range(1, 32): self.assertEqual(c.retrieve(i), BODY)
            self.assertEqual(c.fake.commands.count('RETR'), 31)  # TOP cache reused.
            c.finish()
            self.assertFalse(c.errors, c.errors)
            self.assertEqual(c.classify()['committed'], 31)
            known = (c.root/'delivered').read_bytes()
            self.assertEqual(len(known.splitlines()), 31)
            self.assertEqual(c.fake.commands[-2:], ['RSET', 'QUIT'])
            self.assertNotIn('DELE', c.fake.commands)
            self.assertFalse(list(c.root.glob('full-mail-cache-*')))
        finally: c.cleanup()
        c = Cycle(FakePOP(), known)
        try:
            c.login(); self.assertEqual(c.command(b'STAT'), b'+OK 0 0\r\n'); c.finish()
            self.assertNotIn('RETR', c.fake.commands)
            self.assertFalse(c.errors)
        finally: c.cleanup()

    def test_full_60000_snapshot_finds_old_unknown_outside_recent_windows(self):
        rows = {n: b'u%d' % n for n in range(1, 60001)}
        sizes = {n: 50 for n in rows}
        known = set(rows.values()) - {b'u1', b'u2020', b'u59999'}
        offered, summary = F.choose(rows, sizes, known, set())
        self.assertEqual([x.uid for x in offered], [b'u1', b'u2020', b'u59999'])
        self.assertEqual(summary['untracked_messages'], 3)

    def test_1001_messages_pause_at_1000_then_resume_exactly_one(self):
        c = Cycle(FakePOP(1001))
        try:
            c.login()
            self.assertTrue(c.command(b'STAT').startswith(b'+OK 1000 '))
            for i in range(1, 1001): self.assertEqual(c.retrieve(i), BODY)
            c.finish()
            self.assertEqual(c.classify()['remaining'], 1)
            known = (c.root/'delivered').read_bytes()
            self.assertEqual(len(known.splitlines()), 1000)
        finally: c.cleanup()
        c = Cycle(FakePOP(1001), known)
        try:
            c.login()
            self.assertTrue(c.command(b'STAT').startswith(b'+OK 1 '))
            c.retrieve(1); c.finish()
            self.assertEqual(c.classify()['remaining'], 0)
            self.assertEqual(c.fake.commands.count('RETR'), 1)
        finally: c.cleanup()

    def test_resource_pauses_and_oversize_are_not_caught_up(self):
        rows = {n: b'u%d' % n for n in range(1, 1103)}
        sizes = {n: 50 for n in rows}; sizes[1] = F.MAX_MESSAGE+1
        offered, s = F.choose(rows, sizes, set(), {b'u2'})
        self.assertEqual(len(offered), 1000)
        self.assertEqual((s['resource_paused_messages'], s['oversized_messages'], s['deferred_messages']), (100, 1, 1))
        with patch.object(F, 'MAX_BYTES', 100):
            self.assertEqual(len(F.choose(rows, sizes, set(), set())[0]), 2)

    def test_disconnect_does_not_commit_and_dele_never_upstream(self):
        c = Cycle(FakePOP(2))
        try:
            c.login(); c.retrieve(1)
            self.assertTrue(c.command(b'DELE 1').startswith(b'-ERR'))
            c.finish(quit=False)
            self.assertEqual((c.root/'delivered').read_bytes(), b'')
            self.assertNotIn('DELE', c.fake.commands)
        finally: c.cleanup()

    def test_wrong_uid_and_retr_refusal_fail_without_content_or_ledger(self):
        for fake in (FakePOP(1, bad_identity=True), FakePOP(1, refuse=True)):
            c = Cycle(fake)
            try:
                c.login()
                self.assertFalse(c.command(b'RETR 1').startswith(b'+OK'))
                c.finish(quit=False)
                self.assertTrue(c.errors)
                self.assertEqual((c.root/'delivered').read_bytes(), b'')
                self.assertTrue((c.root/'PROVIDER-ACCESS-DISABLED').exists())
                self.assertNotIn('DELE', fake.commands)
                self.assertNotIn('PRIVATE', (c.root/'evidence/transaction-0001-upstream.txt').read_text())
            finally: c.cleanup()

    def test_malformed_metadata_fails_and_allowlist_rejects_dele(self):
        for rows in ([b'1 same', b'2 same'], [b'1 a'], [b'1 a', b'1 b'], [b'1 a', b'3 b']):
            with self.assertRaises(F.C.SessionBridgeError): F.parse_rows(rows, 2, 'uidl')
        self.assertEqual(F.parse_rows([b'1 a', b'2 b'], 2, 'uidl'), {1:b'a', 2:b'b'})
        # Deliberate fail then success: the negative assertion is not overwritten.
        instance = object.__new__(F.FullPOP)
        with self.assertRaises(F.C.SessionBridgeError): instance._putcmd('DELE 1')
        self.assertNotIn('DELE', F.ALLOWED)

    def test_actual_bulk_metadata_reader_enforces_bytes_rows_and_deadline(self):
        import time
        def reader():
            instance = object.__new__(F.FullPOP)
            instance.deadline = time.monotonic()+10
            instance._getresp = lambda: b'+OK'
            lines = iter([(b'1 abc', 7), (b'.', 3)])
            instance._getline = lambda: next(lines)
            return instance
        self.assertEqual(reader()._getlongresp()[1], [b'1 abc'])
        for key, value in (('MAX_METADATA_BYTES', 8), ('MAX_ROWS', 0)):
            with patch.object(F, key, value):
                with self.assertRaises(F.C.SessionBridgeError): reader()._getlongresp()
        expired = reader(); expired.deadline = 0
        with self.assertRaises(F.C.SessionBridgeError): expired._getlongresp()

    def test_partial_client_selection_reports_remaining_and_classifier_rejects_tampering(self):
        c = Cycle(FakePOP(3))
        try:
            c.login(); c.retrieve(1); c.finish()
            self.assertEqual(c.classify()['remaining'], 2)
            file = c.root/'uid-1-transactions/transaction-0001-upstream.txt'
            good = file.read_text()
            file.write_text(good.replace('remaining_untracked_messages=2', 'remaining_untracked_messages=0'))
            with self.assertRaises(CLASS.C.ClassificationError): CLASS.classify_account(c.root, 1)
            file.write_text(good)
            self.assertEqual(CLASS.classify_account(c.root, 1)['remaining'], 2)
        finally: c.cleanup()

    def test_failed_commit_is_never_retried(self):
        c = Cycle(FakePOP(1))
        calls = []
        def fail(connection, committed):
            calls.append(committed)
            raise F.C.SessionBridgeError('synthetic_commit_failure')
        try:
            c.login(); c.retrieve(1)
            with patch.object(F, 'send_commit', side_effect=fail):
                self.assertEqual(c.command(b'QUIT'), b'')
                c.finish(quit=False)
            self.assertEqual(calls, [{b'u1'}])
            self.assertEqual((c.root/'delivered').read_bytes(), b'')
        finally: c.cleanup()

    def test_publication_then_ack_failure_preserves_truthful_ledger_evidence(self):
        c = Cycle(FakePOP(1))
        # Failure at directory fsync occurs after publication. Track descriptor
        # type so the earlier temp-file fsync and later evidence writes work.
        original_fsync = F.os.fsync
        def fail_directory(fd):
            import stat
            if stat.S_ISDIR(F.os.fstat(fd).st_mode):
                raise OSError('synthetic fsync directory failure')
            return original_fsync(fd)
        try:
            c.login(); c.retrieve(1)
            with patch.object(F.os, 'fsync', side_effect=fail_directory):
                self.assertEqual(c.command(b'QUIT'), b'')
                c.finish(quit=False)
            data = CLASS.C.values(c.root/'evidence/transaction-0001-upstream.txt')
            self.assertEqual((data['status'], data['ledger_published'], data['ledger_durable'], data['committed_messages']),
                             ('failed', 'yes', 'no', '1'))
            self.assertEqual((c.root/'delivered').read_bytes(), b'u1\n')
            self.assertTrue(c.errors)
        finally: c.cleanup()

    def test_lost_commit_ack_reports_published_durable_uids(self):
        c = Cycle(FakePOP(1), ack_loss=True)
        try:
            c.login(); c.retrieve(1)
            self.assertEqual(c.command(b'QUIT'), b'')
            c.finish(quit=False)
            data = CLASS.C.values(c.root/'evidence/transaction-0001-upstream.txt')
            self.assertEqual((data['status'], data['ledger_published'], data['ledger_durable'], data['committed_messages']),
                             ('failed', 'yes', 'yes', '1'))
            self.assertEqual((c.root/'delivered').read_bytes(), b'u1\n')
        finally: c.cleanup()


if __name__ == '__main__': unittest.main()
