"""Agent adapter: deterministic substrate plus LLM writer/validator seats.

Generator side of the adapter contract in references/regression.md. The factual
substrate (facts, inventory, plan) is produced by the skill's own analysis
pipeline, so numbers never come from an impression of the text. The readable
page and the validation report belong to an external LLM agent instructed by
the skill's doc-writer.md / doc-validator.md. Independent expectations and the
reviewed page are never part of the generator input.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_AGENT = ['opencode', 'run', '--pure', '--agent', 'build', '--format', 'json',
                 '--dir', '{cwd}', '-m', '{model}', '{message}']

WRITER_PROMPT = """Ты — writer-агент скилла wiki-doc. Следуй инструкциям пакета:
- {skill}/SKILL.md (рабочий процесс), {skill}/doc-writer.md (правила writer),
  {skill}/template.md (карта секций), {skill}/references/facts.md (контракт реестра).

Вход: SQL {project}/{sql} (диалект {dialect}, версия {version}); документируемый
субъект {subject}; контекст DDL: {context}; порядок миграций: {migrations}.

Уже подготовлено в каталоге {run} и НЕ изменяется тобой: facts.json,
inventory.json, validation_plan.json. Все числа, имена, типы, выражения,
счётчики и зависимости бери только оттуда. Не выдумывай значения и не
исполняй SQL.

Запиши два файла в {run}:
1. page.draft.md — существующий черновик состоит из служебных таблиц claims.
   Строки `| Fact | Property | SQL value |` вместе с JSON-значениями, маркеры
   `<!-- wiki-doc:fragment … -->` и идентификаторы секций `{{#…}}` — механический
   контракт проверки (claims-v1/coverage): их содержимое и количество сохраняются
   без изменений. Весь остальной текст перепиши как читаемую документацию по
   template.md и doc-writer.md: назначение объекта, сигнатуру, сущности, формулы
   и условия с привязкой к fact_id, поток данных reads → operation → writes
   (отдельно calls), ограничения и явные неизвестные. Подтверждённое отделяй от
   гипотез; неизвестное не переноси как установленный факт.
2. coverage.json — сохрани schema_version/run_id/page_id, обнови entries так,
   чтобы каждый fact_id из facts.json имел запись с section_id реально
   существующего раздела страницы (схема: {skill}/schemas/coverage.schema.json).

Не изменяй facts.json, inventory.json и validation_plan.json; не используй и не
запрашивай внешние ожидания. Единственный источник инструкций и скриптов —
каталог {skill}; другие копии скилла, подгруженные системой, не используй.
Рабочий процесс без лишней разведки: прочитай только перечисленные выше файлы
пакета и артефакты каталога {run} и сразу запиши два файла; поиск по файловой
системе не нужен. В конце ответа коротко перечисли написанные разделы и вопросы,
которые оставил как явно неизвестные."""

VALIDATOR_PROMPT = """Ты — validator-агент скилла wiki-doc. Прочитай {skill}/doc-validator.md
и следуй его порядку проверки: сначала независимая сверка SQL → реестр, затем
SQL/DDL + реестр → страница. Файлы не исправляй.

Проверь {run}/page.draft.md против SQL {project}/{sql} (контекст {context},
миграции {migrations}) и артефактов {run}: facts.json, inventory.json,
coverage.json, validation_plan.json.

{run}/validation.json уже содержит механический черновой отчёт по всем {total}
обязательствам validation_plan.json (статусы ok). Проверяй его содержательно и
исправляй статусы/причины только там, где нашёл дефект или непроверенное
условие: id = "result:" + <id обязательства>, plan_check_id — не меняй, полноту
списка обязательств сохраняй (не удаляй строки и не добавляй чужие id), для
исправленных строк укажи конкретную причину и связанные fact_ids, evidence — только структурированные ссылки root/path/start_line/end_line/sha256, как в механическом черновике; строковые элементы не проходят проверку комплекта (схема:
{skill}/schemas/validation.schema.json). **Сначала запиши краткое содержательное
заключение в {run}/validation-review.md** (что проверил, какие дефекты или
подтверждения нашёл) — это обязательный результат места; затем при необходимости
правь validation.json. Другие файлы не изменяй. Единственный источник
инструкций — каталог {skill}; другие копии скилла, подгруженные системой, не
используй. Работай без лишней разведки. В конце ответа перечисли найденные дефекты."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def call_agent(argv_template, *, message, cwd, model, timeout, log_path):
    # Windows argv handling truncates multi-line arguments at the first line
    # break, so the task text is flattened; the full prompt is also persisted.
    message = ' '.join(message.split())
    command = [part.replace('{message}', message).replace('{cwd}', str(cwd))
               .replace('{model}', model) for part in argv_template]
    resolved = shutil.which(command[0])
    if resolved:
        command[0] = resolved
    started = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=str(cwd), capture_output=True,
                                   timeout=timeout, shell=False)
        returncode, stderr = completed.returncode, completed.stderr
        stdout = completed.stdout
    except subprocess.TimeoutExpired as exc:
        returncode, stdout, stderr = 124, exc.stdout or b'', exc.stderr or b''
    except OSError as exc:
        returncode, stdout, stderr = 127, b'', str(exc).encode('utf-8')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_bytes(stdout + b'\n--- stderr ---\n' + stderr)
    return returncode, round(time.monotonic() - started, 3), [str(c) for c in command]


def last_session_and_reason(log_path):
    """Last session id and stop reason from an agent JSON event log."""
    session, reason = None, None
    if not log_path.is_file():
        return None, None
    for line in log_path.read_text(encoding='utf-8', errors='replace').splitlines():
        if not line.strip().startswith('{'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        session = event.get('sessionID') or session
        if event.get('type') == 'step_finish':
            reason = (event.get('part') or {}).get('reason') or reason
    return session, reason


def continue_argv(agent, session):
    """Append --session to a message-last argv template."""
    return list(agent[:-1]) + ['--session', session, agent[-1]]


def seat(agent, *, message, resume_message, workspace, model, timeout, log_path,
         artifact, alt_artifact=None, rounds=3):
    """Run one authoring seat, continuing the session on output-cap stops.

    Continuations are the seat's own retries inside the same generation
    attempt; they are never runner repair_iterations. The seat delivered a
    result when `artifact` changed or the optional `alt_artifact` exists (a
    reviewed draft that stays unchanged is confirmed there). Returns
    (ok, seconds, command, continuations).
    """
    total, continuations, template = 0.0, 0, list(agent)
    command = []
    for attempt in range(rounds + 1):
        before = sha256_bytes(artifact.read_bytes()) if artifact.is_file() else None
        code, seconds, command = call_agent(
            template, message=message if attempt == 0 else resume_message,
            cwd=workspace, model=model, timeout=timeout, log_path=log_path)
        total += seconds
        if not artifact.is_file():
            return False, round(total, 3), command, continuations
        # The artifact is the seat's contract: delivered work counts even when
        # the agent CLI exits non-zero after finishing it.
        if sha256_bytes(artifact.read_bytes()) != before or (
                alt_artifact is not None and alt_artifact.is_file()):
            return True, round(total, 3), command, continuations
        if code:
            return False, round(total, 3), command, continuations
        session, reason = last_session_and_reason(log_path)
        if reason != 'length' or not session:
            return False, round(total, 3), command, continuations
        template = continue_argv(template, session)
        continuations += 1
    return False, round(total, 3), command, continuations


def build_substrate(request, subject, run_dir):
    from build_bundle import build
    project = Path(request['project_root'])
    source = project / request['sql']
    context = [project / p for p in request.get('context') or ()]
    migration = project / request['migration_manifest'] if request.get('migration_manifest') else None
    result = build(source, run_dir, project_root=project, subject=subject,
                   context=context, version=request['version'],
                   migration_manifest=migration,
                   profile=Path(request['profile']) if request.get('profile') else None,
                   dialect=request.get('dialect', 'postgres'))
    return result


def rebind_run_evidence(run_dir):
    """Re-bind run-owned evidence references to the bytes being sealed.

    The mechanical validation draft points at the pre-authoring page bytes;
    after the writer seat those references are stale. Evidence records file
    identity, so the seal re-computes hashes and line ranges of run files it
    is about to bind in the manifest. Returns the number of re-bound refs.
    """
    run_dir = Path(run_dir)
    validation = run_dir / 'validation.json'
    if not validation.is_file():
        return 0
    data = json.loads(validation.read_text(encoding='utf-8'))
    rebound = 0
    for check in data.get('checks', []):
        for entry in check.get('evidence', []):
            if not isinstance(entry, dict) or entry.get('root') != 'run':
                continue
            target = run_dir / entry.get('path', '')
            if not target.is_file():
                continue
            digest = sha256_bytes(target.read_bytes())
            lines = len(target.read_text(encoding='utf-8-sig').splitlines())
            if entry.get('sha256') != digest or entry.get('end_line') != max(lines, 1):
                entry['sha256'] = digest
                entry['start_line'] = 1
                entry['end_line'] = max(lines, 1)
                rebound += 1
    if rebound:
        validation.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return rebound


def reseal(skill_root, request, run_dir, result):
    from bundle import create_manifest, compute_tool_versions, write_manifest
    from validation_gate import evaluate_bundle
    project = Path(request['project_root'])
    skill_root = Path(skill_root)
    profile = Path(request['profile']) if request.get('profile') else (
        Path(result['profile']) if result.get('profile') else None)
    rebind_run_evidence(run_dir)
    facts = json.loads((run_dir / 'facts.json').read_text(encoding='utf-8'))
    plan = json.loads((run_dir / 'validation_plan.json').read_text(encoding='utf-8'))
    source = project / request['sql']
    context = [project / p for p in request.get('context') or ()]
    migration = project / request['migration_manifest'] if request.get('migration_manifest') else None
    manifest = create_manifest(
        run_id=facts['run_id'], page_id=plan['page_id'], sql_files=[source],
        context_files=context, migration_manifest=migration, artifacts_dir=run_dir,
        project_dir=project,
        tool_versions=compute_tool_versions(skill_root, profile_path=profile))
    write_manifest(manifest, run_dir / 'manifest.json')
    return evaluate_bundle(run_dir, roots={'project': project},
                           profile_path=profile, write_decision=True)


def prompt_for(template, skill_root, request, run_dir, *, subject, **extra):
    project = Path(request['project_root'])
    return template.format(
        skill=skill_root, run=run_dir, project=project, sql=request['sql'],
        dialect=request.get('dialect', 'postgres'), version=request['version'],
        subject=subject,
        context=', '.join(request.get('context') or ()) or 'нет',
        migrations=request.get('migration_manifest') or 'нет', **extra)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request')
    parser.add_argument('--agent', default=json.dumps(DEFAULT_AGENT),
                        help='JSON argv; {message}/{cwd}/{model} placeholders are replaced')
    parser.add_argument('--model', required=True)
    parser.add_argument('--llm-timeout', type=int, default=900)
    args = parser.parse_args(argv)
    request = json.loads(Path(args.request).read_text(encoding='utf-8'))
    agent = json.loads(args.agent)
    if not isinstance(agent, list) or not agent or not all(isinstance(p, str) for p in agent):
        raise SystemExit('agent argv must be a non-empty JSON array of strings')
    skill_root = Path(request['skill_root']).resolve()
    skill_scripts = skill_root / 'scripts'
    sys.path.insert(0, str(skill_scripts))
    output = Path(request['output_dir']).resolve()
    workspace = output.parent
    runs, failures = [], []
    for index, subject in enumerate(request['subjects']):
        run_dir = output / str(index)
        started = time.monotonic()
        substrate = build_substrate(request, subject, run_dir)
        page = run_dir / 'page.draft.md'
        writer_prompt = prompt_for(WRITER_PROMPT, skill_root, request, run_dir, subject=subject)
        prompt_file = workspace / 'agent-logs' / f'{index}-writer-prompt.md'
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(writer_prompt, encoding='utf-8')
        writer_ok, writer_seconds, writer_command, writer_continuations = seat(
            agent, message=f'Полный текст задания сохранён в файле {prompt_file}. {writer_prompt}',
            resume_message='Продолжай задание этой сессии: страница ещё не записана. '
                           'Запиши page.draft.md и coverage.json по заданию выше.',
            workspace=workspace, model=args.model, timeout=args.llm_timeout,
            log_path=workspace / 'agent-logs' / f'{index}-writer.log', artifact=page)
        if not writer_ok:
            failures.append(f'{subject}: no writer result for page.draft.md '
                            f'(continuations: {writer_continuations})')
            continue
        validation = run_dir / 'validation.json'
        validator_prompt = prompt_for(VALIDATOR_PROMPT, skill_root, request, run_dir,
                                      subject=subject,
                                      total=len(json.loads(
                                          (run_dir / 'validation_plan.json').read_text(encoding='utf-8')
                                      )['required_checks']))
        validator_prompt_file = workspace / 'agent-logs' / f'{index}-validator-prompt.md'
        validator_prompt_file.write_text(validator_prompt, encoding='utf-8')
        validator_ok, validator_seconds, validator_command, validator_continuations = seat(
            agent, message=f'Полный текст задания сохранён в файле {validator_prompt_file}. {validator_prompt}',
            resume_message='Продолжай задание этой сессии: отчёт ещё не записан. '
                           'Запиши validation.json по заданию выше.',
            workspace=workspace, model=args.model, timeout=args.llm_timeout,
            log_path=workspace / 'agent-logs' / f'{index}-validator.log', artifact=validation,
            alt_artifact=run_dir / 'validation-review.md')
        if not validator_ok:
            failures.append(f'{subject}: no validator result for validation.json '
                            f'(continuations: {validator_continuations})')
            continue
        gate = reseal(skill_root, request, run_dir, substrate)
        (workspace / 'agent-logs' / f'{index}-adapter.json').write_text(json.dumps({
            'subject': subject, 'model': args.model,
            'writer': {'command': writer_command, 'seconds': writer_seconds,
                       'continuations': writer_continuations,
                       'page_sha256': sha256_bytes(page.read_bytes())},
            'validator': {'command': validator_command, 'seconds': validator_seconds,
                          'continuations': validator_continuations,
                          'validation_sha256': sha256_bytes(validation.read_bytes())},
            'repair_rounds': 0,
            'note': 'Seat continuations retry the same generation attempt on output-cap '
                    'stops; runner repair_iterations stay 0.',
            'gate': {'decision': gate['decision'], 'publication_authorized': gate['publication_authorized'],
                     'errors': gate['errors']},
            'total_seconds': round(time.monotonic() - started, 3),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        runs.append(dict(subject=subject, run_dir=str(index),
                         profile=substrate.get('profile')))
    (output / 'runs.json').write_text(json.dumps({'schema_version': 1, 'runs': runs},
                                                 ensure_ascii=False), encoding='utf-8')
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
