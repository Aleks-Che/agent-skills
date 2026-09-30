"""Bounded replacement retries, real Windows share locks, and editor preservation."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / 'scripts'))
import atomic_files
from agent_process import _write_status
import publish
import wiki_store

OLD = b'Preserve the original destination.\n' * 2048
NEW = b'Replace with complete new bytes.\n' * 3072
FOREIGN = b'Unrelated editor change must survive.\n'
DELAYS = [0.05, 0.1, 0.2, 0.4, 0.8, 1.0]


def windows_error(code):
    error = OSError(errno.EACCES, 'injected Windows replacement failure')
    error.winerror = code
    return error


@contextmanager
def deny_delete(path, release_after=None):
    """Keep read/write sharing while denying FILE_SHARE_DELETE."""
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 1 | 2, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    guard = threading.Lock()

    def release():
        nonlocal handle
        with guard:
            if handle is not None:
                if not kernel.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                handle = None

    timer = threading.Timer(release_after, release) if release_after is not None else None
    if timer:
        timer.start()
    try:
        yield release
    finally:
        if timer:
            timer.join()
        release()


class ReplacementFiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        original = self.root / 'original'
        original.mkdir()
        (original / 'page.md').write_bytes(OLD)
        # Match the acceptance trigger: an ordinary writable copytree target.
        self.wiki = self.root / 'copied'
        shutil.copytree(original, self.wiki)
        self.target = self.wiki / 'page.md'
        self.source = self.wiki / 'page.md.new'
        self.source.write_bytes(NEW)


class AtomicReplaceTests(ReplacementFiles):
    def test_success_replaces_complete_bytes_without_sleep(self):
        checks = []
        with patch.object(atomic_files.time, 'sleep') as sleep:
            atomic_files.atomic_replace(self.source, self.target, before_replace=lambda: checks.append(True))
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertFalse(self.source.exists())
        self.assertEqual(checks, [True])
        sleep.assert_not_called()

    def test_exact_windows_codes_retry_and_recheck_each_attempt(self):
        real_replace = os.replace
        for code in (5, 32, 33):
            with self.subTest(code=code):
                self.target.write_bytes(OLD)
                self.source.write_bytes(NEW)
                attempts, checks, sleeps = [], [], []
                def replace(source, target):
                    attempts.append(True)
                    if len(attempts) <= 2:
                        raise windows_error(code)
                    real_replace(source, target)
                with patch.object(atomic_files, 'os', SimpleNamespace(name='nt', replace=replace)), \
                     patch.object(atomic_files.time, 'sleep', side_effect=sleeps.append):
                    atomic_files.atomic_replace(self.source, self.target, before_replace=lambda: checks.append(True))
                self.assertEqual(len(attempts), 3)
                self.assertEqual(len(checks), 3)
                self.assertEqual(sleeps, DELAYS[:2])
                self.assertEqual(self.target.read_bytes(), NEW)

    def test_persistent_retry_budget_keeps_both_files_and_last_error(self):
        for code in (5, 32, 33):
            with self.subTest(code=code):
                error = windows_error(code)
                attempts, checks, sleeps = [], [], []
                def replace(*args):
                    attempts.append(True)
                    raise error
                with patch.object(atomic_files, 'os', SimpleNamespace(name='nt', replace=replace)), \
                     patch.object(atomic_files.time, 'sleep', side_effect=sleeps.append):
                    with self.assertRaises(OSError) as caught:
                        atomic_files.atomic_replace(self.source, self.target, before_replace=lambda: checks.append(True))
                self.assertIs(caught.exception, error)
                self.assertEqual(len(attempts), 7)
                self.assertEqual(len(checks), 7)
                self.assertEqual(sleeps, DELAYS)
                self.assertEqual(self.target.read_bytes(), OLD)
                self.assertEqual(self.source.read_bytes(), NEW)

    def test_other_errors_and_non_windows_fail_immediately(self):
        for platform, code in [('nt', code) for code in (2, 3, 17, 19, 87, 112)] + [('posix', 5)]:
            with self.subTest(platform=platform, code=code):
                error = windows_error(code)
                with patch.object(atomic_files, 'os', SimpleNamespace(name=platform, replace=lambda *_: (_ for _ in ()).throw(error))), \
                     patch.object(atomic_files.time, 'sleep') as sleep:
                    with self.assertRaises(OSError) as caught:
                        atomic_files.atomic_replace(self.source, self.target)
                self.assertIs(caught.exception, error)
                sleep.assert_not_called()
                self.assertEqual(self.target.read_bytes(), OLD)

    def test_precondition_errors_are_never_retried_even_with_windows_error_code(self):
        error = windows_error(5)
        with patch.object(atomic_files.os, 'replace') as replace, \
             patch.object(atomic_files.time, 'sleep') as sleep:
            with self.assertRaises(OSError) as caught:
                atomic_files.atomic_replace(self.source, self.target,
                    before_replace=lambda: (_ for _ in ()).throw(error))
        self.assertIs(caught.exception, error)
        replace.assert_not_called()
        sleep.assert_not_called()
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(self.source.read_bytes(), NEW)

    def test_fsync_failure_does_not_attempt_replacement(self):
        with patch.object(wiki_store.os, 'fsync', side_effect=windows_error(5)), \
             patch.object(wiki_store, 'atomic_replace') as replace:
            with self.assertRaises(OSError):
                wiki_store.atomic_bytes(self.target, NEW)
        replace.assert_not_called()
        self.assertEqual(self.target.read_bytes(), OLD)


@unittest.skipUnless(os.name == 'nt', 'Real Windows share-mode handles')
class WindowsReplacementTests(ReplacementFiles):
    def test_short_destination_lock_is_retried_with_one_fsync(self):
        real_replace, real_fsync = os.replace, os.fsync
        errors = []
        def replace(source, target):
            try:
                return real_replace(source, target)
            except OSError as exc:
                errors.append(exc.winerror)
                raise
        with deny_delete(self.target, release_after=0.18), \
             patch.object(atomic_files.os, 'replace', side_effect=replace), \
             patch.object(wiki_store.os, 'fsync', wraps=real_fsync) as fsync:
            wiki_store.atomic_bytes(self.target, NEW)
        self.assertTrue(errors)
        self.assertTrue(set(errors) <= {5, 32, 33})
        self.assertEqual(fsync.call_count, 1)
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertFalse(self.source.exists())

    def test_short_source_lock_is_retried(self):
        with deny_delete(self.source, release_after=0.18):
            atomic_files.atomic_replace(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertFalse(self.source.exists())

    def test_persistent_real_lock_preserves_destination_bytes_mtime_and_pending_file(self):
        before = self.target.stat().st_mtime_ns
        started = time.perf_counter()
        with deny_delete(self.target):
            with self.assertRaises(OSError) as caught:
                wiki_store.atomic_bytes(self.target, NEW)
            self.assertIn(caught.exception.winerror, (5, 32, 33))
            self.assertEqual(self.target.read_bytes(), OLD)
            self.assertEqual(self.target.stat().st_mtime_ns, before)
            self.assertEqual(self.source.read_bytes(), NEW)
        elapsed = time.perf_counter() - started
        self.assertGreaterEqual(elapsed, sum(DELAYS) - 0.05)
        self.assertLess(elapsed, 8)
        # The exact same pending bytes remain usable once the reader releases.
        os.replace(self.source, self.target)
        self.assertEqual(self.target.read_bytes(), NEW)

    def test_status_writer_uses_the_same_transient_lock_support(self):
        with deny_delete(self.target, release_after=0.18):
            _write_status(self.target, {'state': 'finished', 'returncode': 0})
        self.assertEqual(json.loads(self.target.read_text(encoding='utf-8')),
                         {'state': 'finished', 'returncode': 0})

    def entries(self):
        index = self.wiki / 'index.md'
        index.write_bytes(b'original index\n')
        return [(self.target, NEW), (index, b'generated index\n'),
                (wiki_store.metadata_path(self.wiki, 'page.md'), b'{}\n')]

    def test_publisher_rechecks_precondition_after_real_lock_failure(self):
        entries = self.entries()
        run_id = str(uuid.uuid4())
        lock = deny_delete(self.target)
        release = None
        def fault(point):
            nonlocal release
            if point == 'before_page':
                release = lock.__enter__()
        def edit_after_failure(delay):
            self.target.write_bytes(FOREIGN)
            release()
        try:
            with patch.object(atomic_files.time, 'sleep', side_effect=edit_after_failure) as sleep:
                with self.assertRaises(wiki_store.WikiConflict):
                    publish._commit_files(self.wiki, run_id, entries, fault=fault)
                self.assertEqual(sleep.call_count, 1)
        finally:
            if release is not None:
                lock.__exit__(None, None, None)
        self.assertEqual(self.target.read_bytes(), FOREIGN)
        journal = json.loads((self.wiki / '.wiki-doc/journal' / (run_id + '.json')).read_text())
        self.assertEqual(journal['state'], 'conflict')
        self.assertEqual((self.wiki / journal['files'][0]['backup']).read_bytes(), OLD)
        self.assertEqual((self.wiki / journal['files'][0]['staged']).read_bytes(), NEW)

    def test_recovery_retry_preserves_editor_change_and_does_not_claim_rollback(self):
        entries = self.entries()
        run_id = str(uuid.uuid4())
        lock = deny_delete(self.target)
        release = None
        def fault(point):
            nonlocal release
            if point == 'after_page':
                release = lock.__enter__()
                raise RuntimeError('interrupted publication')
        def edit_after_failure(delay):
            self.target.write_bytes(FOREIGN)
            release()
        try:
            with patch.object(atomic_files.time, 'sleep', side_effect=edit_after_failure) as sleep:
                with self.assertRaises(wiki_store.WikiConflict):
                    publish._commit_files(self.wiki, run_id, entries, fault=fault)
                self.assertEqual(sleep.call_count, 1)
        finally:
            if release is not None:
                lock.__exit__(None, None, None)
        self.assertEqual(self.target.read_bytes(), FOREIGN)
        path = self.wiki / '.wiki-doc/journal' / (run_id + '.json')
        self.assertNotIn(json.loads(path.read_text())['state'], ('rolled_back', 'committed'))
        recovered = publish.recover(self.wiki)
        self.assertEqual(recovered[0]['state'], 'conflict')
        self.assertEqual(self.target.read_bytes(), FOREIGN)
        self.assertEqual(json.loads(path.read_text())['state'], 'conflict')

    def test_recovery_succeeds_after_short_lock_without_editor_change(self):
        entries = self.entries()
        run_id = str(uuid.uuid4())
        lock = deny_delete(self.target, release_after=0.18)
        release = None
        def fault(point):
            nonlocal release
            if point == 'after_page':
                release = lock.__enter__()
                raise RuntimeError('interrupted publication')
        try:
            with self.assertRaisesRegex(RuntimeError, 'interrupted publication'):
                publish._commit_files(self.wiki, run_id, entries, fault=fault)
        finally:
            if release is not None:
                lock.__exit__(None, None, None)
        self.assertEqual(self.target.read_bytes(), OLD)
        journal = json.loads((self.wiki / '.wiki-doc/journal' / (run_id + '.json')).read_text())
        self.assertEqual(journal['state'], 'rolled_back')


if __name__ == '__main__':
    unittest.main()
