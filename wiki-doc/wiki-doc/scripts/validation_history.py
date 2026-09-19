"""Observation history for issuing gate runs; never used to authorize publication."""
from datetime import datetime, timezone
from pathlib import Path

from artifact_schema import read_json, validate_schema
from wiki_store import atomic_json

PACKAGE = Path(__file__).resolve().parents[1]
FILENAME = 'validation-history.json'


def validate_history(history, run_id, page_id):
    schema = read_json(PACKAGE / 'schemas' / 'decision.schema.json')['$defs']['validation_history']
    errors = validate_schema(history, schema, 'validation_history')
    if errors:
        raise ValueError('; '.join(errors))
    if history['run_id'] != run_id or history['page_id'] != page_id:
        raise ValueError('Validation history belongs to another run/page')
    return history


def load_history(run, manifest):
    """Old decisions without observations cannot prove a first-pass success."""
    run_id, page_id = manifest['run_id'], manifest['page_id']
    prior = read_json(run / 'decision.json') if (run / 'decision.json').exists() else {}
    if not isinstance(prior, dict):
        raise ValueError('Invalid prior decision')
    path = run / FILENAME
    history = read_json(path) if path.exists() else prior.get('validation_history')
    if history is not None and not isinstance(history, dict):
        raise ValueError('Invalid validation history')
    if history is not None:
        validate_history(history, history.get('run_id'), history.get('page_id'))
        if history['run_id'] == run_id and history['page_id'] == page_id:
            return validate_history(history, run_id, page_id)
        # Identity corrections are valid gate inputs. Do not reuse observations
        # from another page, or make telemetry override the gate's real verdict.
    return dict(schema_version=1, run_id=run_id, page_id=page_id,
                complete=prior.get('run_id') != run_id, attempts=[])


def record_attempt(run, history, result):
    history['attempts'].append(dict(decision=result['decision'],
                                    recorded_at=datetime.now(timezone.utc).isoformat()))
    atomic_json(run / FILENAME, history)
    record = result.get('decision_record')
    if record is not None:
        record['validation_history'] = history
        atomic_json(run / 'decision.json', record)
