"""Observable, bounded subprocess calls used by the LLM adapter and runner."""
import json
import os
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


def _write_status(path, record):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _stop_tree(process):
    # A Windows .cmd launcher and its LLM children must not survive the timeout.
    errors = []
    tree_terminated = False
    if os.name == 'nt':
        try:
            stopped = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                     capture_output=True, timeout=15, check=False)
            tree_terminated = stopped.returncode == 0
            if not tree_terminated:
                errors.append('taskkill failed: ' + stopped.stderr.decode('utf-8', errors='replace'))
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(str(exc))
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
            tree_terminated = True
        except ProcessLookupError:
            tree_terminated = True
        except OSError as exc:
            errors.append(str(exc))
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(str(exc))
    return dict(tree_terminated=tree_terminated, root_returncode=process.poll(), errors=errors)


def run_logged(command, *, cwd, timeout, stdout_path, stderr_path, env=None):
    """Stream directly to files; persist PID/deadline/result even on failure.

    No prompt text is copied into the status file. A quiet log is not a timeout;
    the explicit wall-clock deadline bounds each call. Return (code, seconds).
    """
    if timeout <= 0:
        raise ValueError('Process timeout must be positive')
    stdout_path, stderr_path = Path(stdout_path), Path(stderr_path)
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    status_path = stdout_path.with_suffix('.status.json')
    started = time.monotonic()
    now = time.time()
    status = dict(state='starting', pid=None, started_at=datetime.fromtimestamp(
        now, timezone.utc).isoformat(), deadline_at=datetime.fromtimestamp(
        now + timeout, timezone.utc).isoformat(), timeout_seconds=timeout,
        stdout=str(stdout_path.resolve()), stderr=str(stderr_path.resolve()))
    _write_status(status_path, status)
    process = None
    code = 127
    with stdout_path.open('wb', buffering=0) as stdout, stderr_path.open('wb', buffering=0) as stderr:
        try:
            options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                       if os.name == 'nt' else {'start_new_session': True})
            process = subprocess.Popen(command, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, shell=False, **options)
            status.update(state='running', pid=process.pid)
            _write_status(status_path, status)
            code = process.wait(timeout=timeout)
            status['state'] = 'completed' if code == 0 else 'failed'
        except subprocess.TimeoutExpired:
            status['state'], code = 'timed_out', 124
            if process is not None:
                status['termination'] = _stop_tree(process)
            stderr.write(f'Process timed out after {timeout} seconds\n'.encode('utf-8'))
        except OSError as exc:
            status['state'], code = 'failed', 127
            if process is not None and process.poll() is None:
                status['termination'] = _stop_tree(process)
            stderr.write(str(exc).encode('utf-8'))
        except BaseException:
            status['state'], code = 'interrupted', 130
            if process is not None:
                status['termination'] = _stop_tree(process)
            raise
        finally:
            seconds = round(time.monotonic() - started, 3)
            status.update(returncode=code, seconds=seconds,
                          finished_at=datetime.now(timezone.utc).isoformat())
            _write_status(status_path, status)
    return code, seconds
