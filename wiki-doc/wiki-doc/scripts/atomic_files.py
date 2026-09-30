"""Atomic replacement with bounded retries for Windows file sharing conflicts."""
import os
import time


def atomic_replace(source, destination, *, before_replace=None):
    # Readers such as indexers can briefly hold a newly written file without
    # FILE_SHARE_DELETE. Never unlink the destination or change its permissions.
    delays = iter((0.05, 0.1, 0.2, 0.4, 0.8, 1.0))
    while True:
        if before_replace is not None:
            before_replace()
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            if os.name != 'nt' or getattr(exc, 'winerror', None) not in (5, 32, 33):
                raise
            delay = next(delays, None)
            if delay is None:
                raise
            time.sleep(delay)
