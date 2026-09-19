"""Opt-in CLI stage observations; never used as gate or publication evidence."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

def start_event(directory, args, version):
    if directory is None:
        return None
    from wiki_store import atomic_json
    event_id = str(uuid4())
    path = Path(directory).resolve() / (event_id + '.json')
    record = dict(schema_version=1, event_id=event_id, package_version=version,
                  command=args.command, state='started',
                  working_directory=str(Path.cwd()),
                  started_at=datetime.now(timezone.utc).isoformat(),
                  context={key: str(getattr(args, key)) for key in
                           ('run_id', 'sql', 'inventory', 'bundle', 'wiki', 'output', 'page_id', 'mode', 'subcmd')
                           if getattr(args, key, None) is not None})
    # One file per invocation avoids a shared append/overwrite across concurrent runs.
    atomic_json(path, record)
    return path, record


def finish_event(event, exit_code):
    if event is None:
        return
    from wiki_store import atomic_json
    path, record = event
    record.update(state='finished', finished_at=datetime.now(timezone.utc).isoformat(),
                  exit_code=exit_code, outcome='success' if exit_code == 0 else 'failure')
    atomic_json(path, record)
