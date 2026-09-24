"""Unified CLI for wiki-doc: inventory, plan, validate, gate, lint, publish, query, metrics, identity, index, bundle, regression, prepare, verify-run, finalize, provenance.

Usage:
  python scripts/wiki_doc.py <subcommand> [options]

Subcommands:
  inventory   Extract SQL inventory from source files
  plan        Generate validation plan from inventory
  validate    Run coverage gate on draft and facts
  gate        Evaluate bundle readiness (validation gate)
  lint        Check wiki state (links, hashes, coverage)
  publish     Prepare, publish or recover wiki pages
  query       Read-only queries over wiki index and lineage
  metrics     Compute wiki health metrics
  identity    Compute or check object identity
  index       Rebuild wiki index and lineage
  bundle      Create or verify artifact bundle
  regression  Run regression scenarios
  prepare     Prepare a verifiable run context (Q-05)
  verify-run  Verify a prepared run context (Q-05)
  finalize    Verify authored artifacts, gate and publish a prepared run (Q-05)
  provenance  Verify selected pages, archives and publisher transactions (Q-05)

Each subcommand delegates to the corresponding module. All existing
scripts remain available as standalone entry points for backward
compatibility. This wrapper does NOT create any bypasses for the
validation gate or publisher.
"""
from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

from _version import __version__


def _add_version(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--version', action='version', version=f'wiki-doc {__version__}')


def _delegate(module_name: str, argv: list[str] | None = None) -> int:
    """Call module.main(); handle both main(argv) and main() signatures."""
    module = __import__(module_name)
    main = module.main
    sig = inspect.signature(main)
    params = list(sig.parameters.values())
    # If main accepts an argument, pass argv; otherwise manipulate sys.argv
    if params and params[0].name in ('argv', 'args'):
        return main(argv)
    else:
        old_argv = sys.argv[:]
        try:
            sys.argv = [module.__file__] + (argv or [])
            return main()
        finally:
            sys.argv = old_argv


def cmd_inventory(argv: list[str] | None = None) -> int:
    """Delegate to sql_extract.py."""
    return _delegate('sql_extract', argv)


def cmd_plan(argv: list[str] | None = None) -> int:
    """Delegate to validation_plan.py."""
    return _delegate('validation_plan', argv)


def cmd_validate(argv: list[str] | None = None) -> int:
    """Delegate to coverage_gate.py."""
    return _delegate('coverage_gate', argv)


def cmd_gate(argv: list[str] | None = None) -> int:
    """Delegate to validation_gate.py."""
    return _delegate('validation_gate', argv)


def cmd_lint(argv: list[str] | None = None) -> int:
    """Delegate to lint.py."""
    return _delegate('lint', argv)


def cmd_publish(argv: list[str] | None = None) -> int:
    """Delegate to publish.py."""
    return _delegate('publish', argv)


def cmd_query(argv: list[str] | None = None) -> int:
    """Delegate to query.py."""
    return _delegate('query', argv)


def cmd_metrics(argv: list[str] | None = None) -> int:
    """Delegate to metrics.py."""
    return _delegate('metrics', argv)


def cmd_identity(argv: list[str] | None = None) -> int:
    """Delegate to identity.py."""
    return _delegate('identity', argv)


def cmd_index(argv: list[str] | None = None) -> int:
    """Delegate to index.py."""
    return _delegate('index', argv)


def cmd_bundle(argv: list[str] | None = None) -> int:
    """Delegate to bundle.py."""
    return _delegate('bundle', argv)


def cmd_regression(argv: list[str] | None = None) -> int:
    """Delegate to run_regression.py."""
    return _delegate('run_regression', argv)


def cmd_prepare(argv: list[str] | None = None) -> int:
    """Delegate to run_prepare.py prepare."""
    return _delegate('run_prepare', (['prepare'] + (argv or [])))


def cmd_verify_run(argv: list[str] | None = None) -> int:
    """Delegate to run_prepare.py verify."""
    return _delegate('run_prepare', (['verify'] + (argv or [])))


def cmd_finalize(argv: list[str] | None = None) -> int:
    """Delegate to run_prepare.py finalize."""
    return _delegate('run_prepare', (['finalize'] + (argv or [])))


def cmd_provenance(argv: list[str] | None = None) -> int:
    """Delegate to run_prepare.py provenance."""
    return _delegate('run_prepare', (['provenance'] + (argv or [])))


SUBCOMMANDS = {
    'inventory': cmd_inventory,
    'plan': cmd_plan,
    'validate': cmd_validate,
    'gate': cmd_gate,
    'lint': cmd_lint,
    'publish': cmd_publish,
    'query': cmd_query,
    'metrics': cmd_metrics,
    'identity': cmd_identity,
    'index': cmd_index,
    'bundle': cmd_bundle,
    'regression': cmd_regression,
    'prepare': cmd_prepare,
    'verify-run': cmd_verify_run,
    'finalize': cmd_finalize,
    'provenance': cmd_provenance,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='wiki-doc',
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_version(parser)
    parser.add_argument('subcommand', nargs='?', choices=sorted(SUBCOMMANDS),
                        help='Subcommand to run')
    parser.add_argument('args', nargs=argparse.REMAINDER,
                        help='Arguments for the subcommand')

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 2

    handler = SUBCOMMANDS[args.subcommand]
    return handler(args.args)


if __name__ == '__main__':
    sys.exit(main())
