"""Failure-phase truthfulness and privacy; only synthetic Unix-socket traffic."""
import json
import poplib
import tempfile
import unittest
from pathlib import Path
from test_full_mail import W, F, Cycle, FakePOP
import account_state as S
import diagnostics as D


class DiagnosticTests(unittest.TestCase):
    def test_exception_categories_do_not_copy_provider_text(self):
        for error, expected in [
            (poplib.error_proto('-ERR EOF'), 'connection_closed'),
            (poplib.error_proto(b'-ERR EOF'), 'server_error_response'),
            (poplib.error_proto('line too long'), 'response_line_too_long'),
            (poplib.error_proto(b'-ERR PRIVATE PASSWORD user@example.invalid'), 'server_error_response'),
            (poplib.error_proto(b'PRIVATE unexpected response'), 'protocol_error'),
            (TimeoutError('PRIVATE'), 'timeout'),
            (OSError('PRIVATE'), 'transport_error'),
        ]:
            self.assertEqual(F.failure_detail(error), expected)

    def test_list_failure_after_login_is_not_labeled_bad_password(self):
        for error, expected in [(poplib.error_proto('-ERR EOF'), 'connection_closed'),
                                (poplib.error_proto(b'-ERR PRIVATE'), 'server_error_response')]:
            fake = FakePOP(1)
            def fail_list():
                fake.commands.append('LIST')
                raise error
            fake.list = fail_list
            c = Cycle(fake)
            try:
                self.assertTrue(c.command(b'USER user').startswith(b'+OK'))
                self.assertTrue(c.command(b'PASS secret').startswith(b'-ERR'))
                c.finish(quit=False)
                report = c.root/'evidence/transaction-0001-upstream.txt'
                text = report.read_text()
                self.assertIn('login_completed=yes', text)
                self.assertIn('issue_phase=list', text)
                self.assertIn('failure_detail='+expected, text)
                self.assertNotIn('PRIVATE', text)
                self.assertNotIn('secret', text)
                self.assertEqual((c.root/'delivered').read_bytes(), b'')
                self.assertTrue((c.root/'PROVIDER-ACCESS-DISABLED').exists())
                D.record(c.root, report)
                message = D.describe(c.root)
                self.assertIn('Provider login succeeded', message)
                self.assertIn('listing message sizes failed', message)
                self.assertNotIn('PRIVATE', message)
            finally:
                c.cleanup()

    def test_failed_password_phase_does_not_claim_login_succeeded(self):
        fake = FakePOP(1)
        def fail_password(_): raise poplib.error_proto(b'-ERR PRIVATE')
        fake.pass_ = fail_password
        c = Cycle(fake)
        try:
            c.command(b'USER user')
            self.assertTrue(c.command(b'PASS secret').startswith(b'-ERR'))
            c.finish(quit=False)
            D.record(c.root, c.root/'evidence/transaction-0001-upstream.txt')
            self.assertIn('Provider login was not completed', D.describe(c.root))
        finally:
            c.cleanup()

    def test_missing_invalid_and_untrusted_notices_fall_back(self):
        with tempfile.TemporaryDirectory(dir=W/'records') as tmp:
            root = Path(tmp)
            self.assertIsNone(D.describe(root))
            cases = [b'{', b'{"login":"yes","login":"no"}', b'x'*1025,
                     json.dumps(dict(phase='list', detail='PRIVATE', login='yes')).encode(),
                     json.dumps(dict(phase='pass', detail='server_error_response', login='yes')).encode(),
                     json.dumps(dict(phase='list', detail='timeout', login='yes', raw='PRIVATE')).encode()]
            for data in cases:
                file = root/'failure.json'
                S.new_file(file, data)
                self.assertIsNone(D.describe(root))
                file.unlink()

    def test_report_duplicate_fields_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=W/'records') as tmp:
            root = Path(tmp)
            report = root/'report'
            S.new_file(report, b'status=failed\nstatus=complete\n')
            with self.assertRaises(ValueError): D.record(root, report)
            self.assertFalse((root/'failure.json').exists())
