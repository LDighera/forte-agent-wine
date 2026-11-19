"""Synthetic attachment-folder boundary tests; no user media inspected."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import attachment_folders as F
import account_state as S
import debian_launcher as L
import test_debian_launcher as T


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        T.LauncherTests.setUp(self)
        self.home = self.root/'home'
        self.home.mkdir()
        self.media = self.home/'Attachments'
        self.media.mkdir()
        self.mount = dict(target='/',filesystem='tmpfs',root='/',identity='source:fixture')

    def rows(self, mode='ro', path=None):
        return [dict(path=str(path or self.media),mode=mode)]

    def pin(self, rows=None, protected=()):
        with patch.object(F, 'identity', return_value=self.mount):
            return F.validate(self.rows() if rows is None else rows, protected=list(protected),home=self.home,pin=True)

    def test_readonly_and_writable_preserve_paths(self):
        for mode in ('ro','rw'):
            result = self.pin(self.rows(mode))
            self.assertEqual(result,[dict(path=str(self.media),mode=mode,mount=self.mount)])

    def test_symlinks_missing_and_entire_home_rejected(self):
        alias = self.home/'alias'; alias.symlink_to(self.media)
        for path in (alias,self.home,self.root,self.home/'missing',Path('/')):
            with self.subTest(path=path), self.assertRaises(ValueError): self.pin(self.rows(path=path))

    def test_hidden_home_and_protected_overlap_rejected(self):
        secret=self.home/'.ssh';secret.mkdir()
        with self.assertRaises(S.StateError):self.pin(self.rows(path=secret))
        for protected in ([self.media],[self.media/'account.json'],[self.home]):
            with self.assertRaises(S.StateError):self.pin(protected=protected)

    def test_duplicates_nested_and_invalid_mode_rejected(self):
        child=self.media/'child';child.mkdir()
        for rows in (self.rows()*2,self.rows()+self.rows(path=child),self.rows(mode='all'),self.rows()*17):
            with self.assertRaises(S.StateError):self.pin(rows)

    def test_changed_mount_stops_before_launch(self):
        rows=self.pin()
        with patch.object(F,'identity',return_value=dict(self.mount,identity='source:other')):
            with self.assertRaisesRegex(S.StateError,'mount_changed'):
                F.validate(rows,protected=[],home=self.home)

    def test_missing_access_rejected(self):
        with patch.object(F.os,'access',return_value=False):
            with self.assertRaisesRegex(S.StateError,'permission_denied'):self.pin()

    def test_findmnt_identity_prefers_uuid(self):
        row=dict(target='/media/fixture',source='/dev/example',fstype='ext4',fsroot='/',uuid='test-uuid')
        with patch.object(F.subprocess,'run',return_value=Mock(returncode=0,stdout=json.dumps({'filesystems':[row]}))):
            self.assertEqual(F.identity(self.media)['identity'],'uuid:test-uuid')

    def test_only_client_exposes_selected_folders_with_requested_access(self):
        for mode,flag in [('ro','--ro-bind'),('rw','--bind')]:
            rows=self.pin(self.rows(mode))
            with patch.object(F,'identity',return_value=self.mount),patch.object(F.Path,'home',return_value=self.home), \
                 patch.object(L,'PACKAGE',self.root/'package/pop-bridge'):
                client=L.sandbox('client',self.config_path,self.config,self.account,self.run,['APP'],attachment_folders=rows)
                broker=L.sandbox('broker',self.config_path,self.config,self.account,self.run,['APP'],attachment_folders=rows)
            index=client.index(str(self.media))
            self.assertEqual(client[index-1:index+2],[flag,str(self.media),str(self.media)])
            self.assertNotIn(str(self.media),broker)

    def test_nested_mount_is_rejected(self):
        with patch.object(F,'mount_points',return_value=[self.media/'alias']):
            with self.assertRaisesRegex(S.StateError,'nested_mount'):self.pin()

    def test_bind_alias_of_private_directory_is_rejected(self):
        private=self.root/'private';private.mkdir()
        original=F.location
        def alias(path,mount=None):
            if path==self.media:return private.stat().st_dev,private
            return original(path,mount)
        with patch.object(F,'location',side_effect=alias):
            with self.assertRaisesRegex(S.StateError,'alias_overlap'):self.pin(protected=[private])

    def test_bind_alias_of_hidden_home_is_rejected(self):
        original=F.location
        def alias(path,mount=None):
            if path==self.media:return self.home.stat().st_dev,self.home/'.ssh'
            return original(path,mount)
        with patch.object(F,'location',side_effect=alias):
            with self.assertRaisesRegex(S.StateError,'private_home'):self.pin()
