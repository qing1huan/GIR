"""Frozen public archive verification only; no archive code is executed."""
import contextlib
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import tarfile

from ark_eval.adapter import EvaluationError
from ark_eval import pilot24_public_payload as p


class FrozenPublicPayloadTests(unittest.TestCase):
    def test_recovered_701_files_match_original_freeze(self):
        files=p.frozen_public_payload()
        expected=p.read(p.PUBLIC_RUNTIME)['files_sha256']
        self.assertEqual(len(files),701)
        self.assertEqual({n:hashlib.sha256(b).hexdigest() for n,b in files.items()},expected)

    def test_current_repository_source_drift_is_not_hidden(self):
        with patch.object(p,'sha',return_value='0'*64):
            with self.assertRaisesRegex(EvaluationError,'REPOSITORY_SOURCE_DRIFT'):
                p.frozen_public_payload()

    def fixture(self, extra=None, omit=None, change=None):
        names=['gir-python/public-mock-%04d.py'%i for i in range(701)]
        expected={n:hashlib.sha256(b'public mock').hexdigest() for n in names}
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w') as archive:
            for n in names:
                if n==omit:continue
                data=b'changed' if n==change else b'public mock'
                m=tarfile.TarInfo(n);m.size=len(data)
                archive.addfile(m,io.BytesIO(data))
            if extra:
                name,kind=extra
                m=tarfile.TarInfo(name)
                if kind=='link':m.type=tarfile.SYMTYPE;m.linkname=names[0]
                elif kind=='hardlink':m.type=tarfile.LNKTYPE;m.linkname=names[0]
                else:m.size=11
                archive.addfile(m,None if kind in ('link','hardlink') else io.BytesIO(b'public mock'))
        return buffer.getvalue(),expected,names

    def load_fixture(self, content, expected, **options):
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            root=Path(folder);(root/'public.tar').write_bytes(content)
            for name,value in {'ROOT':root,'ARCHIVE':'public.tar',
                              'ARCHIVE_SHA':hashlib.sha256(content).hexdigest(),**options}.items():
                stack.enter_context(patch.object(p,name,value))
            stack.enter_context(patch.object(p,'read',return_value={'files_sha256':expected}))
            stack.enter_context(patch.object(p,'source_paths',return_value={}))
            return p.frozen_public_payload()

    def test_missing_archive_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(p,'ROOT',Path(folder)):
            with self.assertRaisesRegex(EvaluationError,'ARCHIVE_PATH'):
                p.frozen_public_payload()

    def test_archive_digest_and_size_are_enforced(self):
        content,expected,_=self.fixture()
        for option in ({'ARCHIVE_SHA':'0'*64},{'ARCHIVE_LIMIT':len(content)-1}):
            with self.subTest(option=option), self.assertRaisesRegex(EvaluationError,'ARCHIVE_DIGEST'):
                self.load_fixture(content,expected,**option)

    def test_extra_duplicate_traversal_and_link_members_rejected(self):
        for extra in (('extra.py','file'),('gir-python/public-mock-0000.py','file'),
                      ('../outside.py','file'),('/outside.py','file'),
                      ('gir-python/public-mock-0000.py','link'),
                      ('gir-python/public-mock-0000.py','hardlink')):
            with self.subTest(extra=extra), self.assertRaisesRegex(EvaluationError,'ARCHIVE_MEMBER'):
                content,expected,_=self.fixture(extra=extra)
                self.load_fixture(content,expected)

    def test_in_allowlist_link_is_rejected_even_without_duplicate(self):
        name='gir-python/public-mock-0000.py'
        content,expected,_=self.fixture(omit=name,extra=(name,'link'))
        with self.assertRaisesRegex(EvaluationError,'ARCHIVE_MEMBER'):
            self.load_fixture(content,expected)

    def test_missing_member_and_changed_bytes_rejected(self):
        name='gir-python/public-mock-0000.py'
        content,expected,_=self.fixture(omit=name)
        with self.assertRaisesRegex(EvaluationError,'ARCHIVE_FILE_SET'):
            self.load_fixture(content,expected)
        content,expected,_=self.fixture(change=name)
        with self.assertRaisesRegex(EvaluationError,'ARCHIVE_CONTENT'):
            self.load_fixture(content,expected)

    def test_member_and_aggregate_resource_limits_enforced(self):
        content,expected,_=self.fixture()
        for option,code in (({'MEMBER_LIMIT':10},'ARCHIVE_MEMBER'),({'TOTAL_LIMIT':100},'TOTAL_LIMIT')):
            with self.subTest(option=option), self.assertRaisesRegex(EvaluationError,code):
                self.load_fixture(content,expected,**option)


if __name__=='__main__':unittest.main()
