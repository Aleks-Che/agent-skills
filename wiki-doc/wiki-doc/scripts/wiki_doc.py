"""Unified CLI wrapper for wiki-doc operations.

Single entry point for inventory, plan, validate, lint, publish, query, metrics,
and identity operations.  Reuses the same gate and publisher — no bypasses.

Usage:
    python scripts/wiki_doc.py <command> [options]

Commands:
    inventory   — extract SQL inventory from source file
    plan        — generate validation plan from inventory
    prepare     — preserve manual blocks and capture publication snapshots
    validate    — run validation gate on a bundle
    lint        — read-only wiki lint
    publish     — publish validated bundle to wiki
    recover     — recover interrupted publication transactions
    query       — query wiki graph (page, dependencies, consumers, affected, outdated)
    metrics     — compute wiki quality metrics
    identity    — compute or check object identity
    version     — print package version
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _version() -> str:
    vfile = Path(__file__).resolve().parents[1] / 'VERSION'
    if vfile.exists():
        return vfile.read_text(encoding='utf-8').strip()
    return '0.0.0-dev'


# ---------------------------------------------------------------------------
# Subcommand: inventory
# ---------------------------------------------------------------------------

def cmd_inventory(args: argparse.Namespace) -> int:
    from sql_extract import main as inventory_main
    argv = [args.sql, '--dialect', args.dialect, '--version', args.version]
    for option in ('run_id', 'project_root', 'migration_manifest', 'output'):
        if getattr(args, option):
            argv += ['--' + option.replace('_', '-'), getattr(args, option)]
    for option in ('subjects', 'context'):
        if getattr(args, option) is not None:
            argv += ['--' + option, *getattr(args, option)]
    return inventory_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------

def cmd_plan(args: argparse.Namespace) -> int:
    from validation_plan import main as plan_main
    argv = [args.inventory]
    for option in ('policy', 'page_id', 'kind', 'object_key', 'profile', 'output'):
        if getattr(args, option):
            argv += ['--' + option.replace('_', '-'), getattr(args, option)]
    if args.subjects is not None:
        argv += ['--subjects', *args.subjects]
    if args.profile_active:
        argv.append('--profile-active')
    return plan_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: validate
# ---------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    from validation_gate import main as gate_main
    argv = ['--bundle', str(Path(args.bundle).resolve())]
    for name, path in (args.root or []):
        argv += ['--root', name, str(Path(path).resolve())]
    if args.policy:
        argv += ['--policy', str(Path(args.policy).resolve())]
    if args.profile:
        argv += ['--profile', str(Path(args.profile).resolve())]
    if args.write_decision:
        argv.append('--write-decision')
    if args.json:
        argv.append('--json')
    return gate_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: lint
# ---------------------------------------------------------------------------

def cmd_lint(args: argparse.Namespace) -> int:
    from lint import main as lint_main
    argv = [str(Path(args.wiki).resolve())]
    if args.project_root:
        argv += ['--project-root', str(Path(args.project_root).resolve())]
    if args.json:
        argv.append('--json')
    return lint_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: publish
# ---------------------------------------------------------------------------

def cmd_publish(args: argparse.Namespace) -> int:
    from publish import main as publish_main
    argv = ['publish', str(Path(args.bundle).resolve()),
            '--wiki', str(Path(args.wiki).resolve())]
    if args.project_root:
        argv += ['--project-root', str(Path(args.project_root).resolve())]
    for option in ('profile', 'policy'):
        if getattr(args, option):
            argv += ['--' + option, getattr(args, option)]
    if args.dry_run:
        argv.append('--dry-run')
    if args.cleanup_run:
        argv.append('--cleanup-run')
    return publish_main(argv)


def cmd_prepare(args: argparse.Namespace) -> int:
    from publish import main as publish_main
    return publish_main(['prepare', args.bundle, '--wiki', args.wiki])


def cmd_recover(args: argparse.Namespace) -> int:
    from publish import main as publish_main
    return publish_main(['recover', '--wiki', args.wiki])


# ---------------------------------------------------------------------------
# Subcommand: query
# ---------------------------------------------------------------------------

def cmd_query(args: argparse.Namespace) -> int:
    from query import main as query_main
    argv = [str(Path(args.wiki).resolve()), '--max-depth', str(args.max_depth)]
    if args.mode == 'outdated':
        argv.append('--outdated')
    else:
        if not args.target:
            print('query: --target is required for mode ' + args.mode, file=sys.stderr)
            return 2
        argv += ['--' + args.mode, args.target]
    if args.json:
        argv.append('--json')
    return query_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: metrics
# ---------------------------------------------------------------------------

def cmd_metrics(args: argparse.Namespace) -> int:
    from metrics import main as metrics_main
    argv = [str(Path(args.wiki).resolve())]
    if args.regression_report:
        argv += ['--regression-report', str(Path(args.regression_report).resolve())]
    if args.json:
        argv.append('--json')
    return metrics_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: identity
# ---------------------------------------------------------------------------

def cmd_identity(args: argparse.Namespace) -> int:
    from identity import main as identity_main
    argv = [args.subcmd]
    if args.subcmd == 'compute':
        argv += ['--kind', args.kind]
        if args.schema:
            argv += ['--schema', args.schema]
        if args.name:
            argv += ['--name', args.name]
        if args.arg_types:
            argv += ['--arg-types', *args.arg_types]
        if args.migration_path:
            argv += ['--migration-path', args.migration_path]
        if args.migration_id:
            argv += ['--migration-id', args.migration_id]
        for option in ('registry', 'legacy_keys'):
            if getattr(args, option):
                argv += ['--' + option.replace('_', '-'), getattr(args, option)]
        if args.existing_ids:
            argv += ['--existing-ids', *args.existing_ids]
    elif args.subcmd == 'check':
        argv += [args.canonical_key, '--existing-ids', *args.existing_ids]
    return identity_main(argv)


# ---------------------------------------------------------------------------
# Subcommand: version
# ---------------------------------------------------------------------------

def cmd_version(_args: argparse.Namespace) -> int:
    print(_version())
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog='wiki-doc', description='Wiki-doc unified CLI')
    p.add_argument('--journal', type=Path, help='Opt-in directory for durable command-stage events')
    sub = p.add_subparsers(dest='command', required=True)

    # inventory
    inv = sub.add_parser('inventory', help='Extract SQL inventory')
    inv.add_argument('sql', help='SQL source file')
    inv.add_argument('--subjects', nargs='*', help='Documented subject scopes')
    inv.add_argument('--dialect', default='postgres', help='SQL dialect')
    inv.add_argument('--version', default='unknown', help='DB version')
    inv.add_argument('--run-id', help='UUID shared by all artifacts of this run')
    inv.add_argument('--project-root', help='SQL project root for relative source paths')
    inv.add_argument('--context', nargs='*', help='DDL/context source files')
    inv.add_argument('--migration-manifest', help='Explicit migration order')
    inv.add_argument('-o', '--output', help='Output file (default: stdout)')
    inv.set_defaults(func=cmd_inventory)

    # plan
    pl = sub.add_parser('plan', help='Generate validation plan')
    pl.add_argument('inventory', help='inventory.json path')
    pl.add_argument('--policy', help='check-policy.json path')
    pl.add_argument('--page-id', help='Page identifier')
    pl.add_argument('--kind', help='Object kind (function, table, etc.)')
    pl.add_argument('--subjects', nargs='*', help='Documented subject scopes')
    pl.add_argument('--object-key', help='Canonical object key')
    pl.add_argument('--profile-active', action='store_true', help='Include CKR_GP obligations')
    pl.add_argument('--profile', help='Explicit profile JSON/Markdown path')
    pl.add_argument('-o', '--output', help='Output file')
    pl.set_defaults(func=cmd_plan)

    # validate
    val = sub.add_parser('validate', help='Run validation gate')
    val.add_argument('bundle', help='Run directory with artifacts')
    val.add_argument('--root', nargs=2, action='append', metavar=('NAME', 'PATH'),
                     help='Named root (repeatable): --root project /path --root wiki /path')
    val.add_argument('--policy', help='check-policy.json path')
    val.add_argument('--profile', help='Profile JSON path')
    val.add_argument('--write-decision', action='store_true', help='Write decision.json')
    val.add_argument('--json', action='store_true', help='JSON output')
    val.set_defaults(func=cmd_validate)

    # lint
    li = sub.add_parser('lint', help='Wiki lint (read-only)')
    li.add_argument('wiki', help='Wiki directory')
    li.add_argument('--project-root', help='Project root directory')
    li.add_argument('--json', action='store_true', help='JSON output')
    li.set_defaults(func=cmd_lint)

    # publish
    pub = sub.add_parser('publish', help='Publish to wiki')
    pub.add_argument('bundle', help='Run directory')
    pub.add_argument('wiki', help='Wiki directory')
    pub.add_argument('--project-root', help='Project root')
    pub.add_argument('--profile', help='Profile used by validation')
    pub.add_argument('--policy', help='Check policy used by validation')
    pub.add_argument('--dry-run', action='store_true', help='Show changes without writing')
    pub.add_argument('--cleanup-run', action='store_true', help='Remove run dir after commit')
    pub.add_argument('--json', action='store_true', help='JSON output')
    pub.set_defaults(func=cmd_publish)

    prep = sub.add_parser('prepare', help='Preserve manual blocks and prepare publication snapshots')
    prep.add_argument('bundle', help='Run directory')
    prep.add_argument('wiki', help='Wiki directory')
    prep.set_defaults(func=cmd_prepare)
    rec = sub.add_parser('recover', help='Recover unfinished publication transactions')
    rec.add_argument('wiki', help='Wiki directory')
    rec.set_defaults(func=cmd_recover)

    # query
    q = sub.add_parser('query', help='Query wiki graph')
    q.add_argument('wiki', help='Wiki directory')
    q.add_argument('--mode', required=True,
                   choices=['page', 'dependencies', 'consumers', 'affected', 'outdated'],
                   help='Query mode')
    q.add_argument('--target', help='page_id for page mode; canonical_key for dependency modes')
    q.add_argument('--max-depth', type=int, default=32, help='Maximum dependency traversal depth')
    q.add_argument('--json', action='store_true', help='JSON output')
    q.set_defaults(func=cmd_query)

    # metrics
    met = sub.add_parser('metrics', help='Wiki quality metrics')
    met.add_argument('wiki', help='Wiki directory')
    met.add_argument('--regression-report', help='Mutation report path')
    met.add_argument('--json', action='store_true', help='JSON output')
    met.set_defaults(func=cmd_metrics)

    # identity
    ident = sub.add_parser('identity', help='Object identity')
    ident_sub = ident.add_subparsers(dest='subcmd', required=True)
    ic = ident_sub.add_parser('compute', help='Compute identity')
    ic.add_argument('--kind', required=True, help='Object kind')
    ic.add_argument('--schema', help='Schema name (not required for migrations)')
    ic.add_argument('--name', help='Object name (not required for migrations)')
    ic.add_argument('--arg-types', nargs='*', help='Argument types (functions)')
    ic.add_argument('--migration-path', help='Migration file path')
    ic.add_argument('--migration-id', help='Migration ID')
    ic.add_argument('--registry', help='Existing path-to-key JSON registry')
    ic.add_argument('--legacy-keys', help='Old-key-to-current-key JSON mapping')
    ic.add_argument('--existing-ids', nargs='*', default=[], help='Occupied page IDs')
    ic.add_argument('--json', action='store_true', help='JSON output')
    ic.set_defaults(func=cmd_identity)
    ich = ident_sub.add_parser('check', help='Check collision against existing page IDs')
    ich.add_argument('canonical_key', help='Canonical key to check')
    ich.add_argument('--existing-ids', nargs='+', required=True, help='Existing page IDs')
    ich.set_defaults(func=cmd_identity)

    # version
    ver = sub.add_parser('version', help='Print version')
    ver.set_defaults(func=cmd_version)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from stage_journal import start_event, finish_event
    try:
        event = start_event(args.journal, args, _version())
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': str(exc), 'command_started': False}), file=sys.stderr)
        return 2
    try:
        rc = args.func(args)
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 2
    except (OSError, ValueError, ImportError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=True), file=sys.stderr)
        rc = 2
    try:
        finish_event(event, rc)
    except (OSError, ValueError) as exc:
        print(json.dumps({'error': 'Stage journal update failed: ' + str(exc),
                          'command_exit_code': rc, 'command_finished': True}), file=sys.stderr)
        return 2
    return rc


if __name__ == '__main__':
    sys.exit(main())
