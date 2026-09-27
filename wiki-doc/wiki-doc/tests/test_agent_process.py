"""Observable logs and deadlines for real subprocesses (no LLM required)."""
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from agent_process import run_logged


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out, self.err = self.root / 'stdout.log', self.root / 'stderr.log'

    def run_child(self, script, timeout=10):
        return run_logged([sys.executable, '-u', '-c', script], cwd=self.root,
                          timeout=timeout, stdout_path=self.out, stderr_path=self.err)

    def status(self):
        return json.loads(self.out.with_suffix('.status.json').read_text(encoding='utf-8'))

    def test_logs_and_pid_are_visible_before_process_exit(self):
        release = self.root / 'release'
        result = []
        thread = threading.Thread(target=lambda: result.append(self.run_child(
            "import sys,time\nfrom pathlib import Path\n"
            "print('working', flush=True)\nprint('diagnostic', file=sys.stderr, flush=True)\n"
            f"while not Path({str(release)!r}).exists(): time.sleep(.02)\n")))
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if self.out.exists() and b'working' in self.out.read_bytes():
                    break
                time.sleep(.02)
            self.assertTrue(thread.is_alive())
            self.assertIn(b'working', self.out.read_bytes())
            self.assertIn(b'diagnostic', self.err.read_bytes())
            self.assertEqual(self.status()['state'], 'running')
            self.assertGreater(self.status()['pid'], 0)
        finally:
            release.touch()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 0)
        self.assertEqual(self.status()['state'], 'completed')

    def test_timeout_preserves_output_and_stops_descendants(self):
        marker = self.root / 'orphan-result'
        child = f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).touch()"
        code, _ = self.run_child(
            f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child!r}])\n"
            "print('started',flush=True)\ntime.sleep(30)\n", timeout=.6)
        self.assertEqual(code, 124)
        self.assertIn(b'started', self.out.read_bytes())
        self.assertEqual(self.status()['state'], 'timed_out')
        self.assertTrue(self.status()['termination']['tree_terminated'], self.status())
        time.sleep(2)
        self.assertFalse(marker.exists(), 'Timed-out child continued writing')

    def test_launch_failure_is_persisted(self):
        code, _ = run_logged([str(self.root / 'missing-program')], cwd=self.root,
                             timeout=2, stdout_path=self.out, stderr_path=self.err)
        self.assertEqual(code, 127)
        self.assertEqual(self.status()['state'], 'failed')
        self.assertIsNone(self.status()['pid'])
        self.assertTrue(self.err.read_bytes())

    def test_nonzero_exit_is_not_reported_as_completed(self):
        self.assertEqual(self.run_child('import sys; sys.exit(7)')[0], 7)
        self.assertEqual(self.status()['state'], 'failed')


if __name__ == '__main__':
    unittest.main()
