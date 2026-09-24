"""Focused resume controls using real prepared contexts and complete bundles."""
import argparse
import unittest
from unittest.mock import patch

if __package__:
    from . import test_q05_finalize_review as fixture
else:
    import test_q05_finalize_review as fixture
import run_prepare
from wiki_store import atomic_json


class ResumeTests(unittest.TestCase):
    setUp = fixture.FinalizeReviewTests.setUp
    call = fixture.FinalizeReviewTests.call
    authored = fixture.FinalizeReviewTests.authored

    def test_missing_run_context_is_blocked(self):
        result = self.call(run_prepare.cmd_resume, argparse.Namespace(run_dir=str(self.root/'missing')), 1)
        self.assertEqual(result['status'], 'blocked')

    def test_limitation_run_is_rejected(self):
        atomic_json(self.run/'limitation.json', {'status': 'limitation'})
        result = self.call(run_prepare.cmd_resume, argparse.Namespace(run_dir=str(self.run)), 1)
        self.assertEqual(result['status'], 'blocked')

    def test_partial_run_requires_writer_action(self):
        before = fixture.snapshot(self.run)
        result = self.call(run_prepare.cmd_resume, argparse.Namespace(run_dir=str(self.run)), 1)
        self.assertEqual(result['status'], 'needs_action')
        self.assertEqual(result['next_stage'], 'facts')
        self.assertFalse(result['generation_completed'])
        self.assertEqual(before, fixture.snapshot(self.run))

    def test_complete_authoring_delegates_to_real_finalize(self):
        self.authored()
        with patch('run_prepare.cmd_finalize', wraps=run_prepare.cmd_finalize) as finalize:
            result = self.call(run_prepare.cmd_resume, argparse.Namespace(run_dir=str(self.run)), 0)
        finalize.assert_called_once()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['run_id'], self.run_id)
        self.assertTrue((self.wiki/self.pid).is_file())


if __name__ == '__main__':
    unittest.main()
