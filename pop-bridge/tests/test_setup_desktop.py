import json
import unittest
from unittest.mock import patch

import setup_desktop as Q
import test_desktop as T
import account_state as S


class SetupDesktopTests(unittest.TestCase):
    setUp = T.DesktopTests.setUp

    def plan(self):
        with patch.object(Q.C, 'parse', return_value=self.config):
            return Q.prepare(self.root/'new', self.prefix, self.wine, self.config_path, browser=False)

    def test_prepare_is_readonly_and_create_never_clobbers(self):
        plan = self.plan()
        self.assertFalse(plan[0].exists())
        Q.create(plan)
        raw = (plan[0]/'desktop.json').read_bytes()
        self.assertFalse(json.loads(raw)['browser'])
        self.assertEqual((plan[0]/'runs').stat().st_mode & 0o777, 0o700)
        with self.assertRaises(FileExistsError): Q.create(plan)
        self.assertEqual((plan[0]/'desktop.json').read_bytes(), raw)

    def test_writable_prefix_rejected_before_creation(self):
        self.prefix.chmod(0o777)
        with self.assertRaises(S.StateError): self.plan()
        self.assertFalse((self.root/'new').exists())

    def test_existing_destination_rejected(self):
        (self.root/'new').mkdir()
        with self.assertRaises(S.StateError): self.plan()
