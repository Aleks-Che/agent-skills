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
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from agent_process import run_logged
from artifact_schema import read_json

DEFAULT_AGENT = ['opencode', 'run', '--pure', '--agent', 'build', '--format', 'json',
                 '--dir', '{cwd}', '-m', '{model}', '{message}']

FILE_ACCESS_NOTE = """
Файлы задания уже перечислены: не обходи каталоги рекурсивно и не ищи другие
копии скилла. Все файлы — UTF-8. Для PowerShell чтения задавай одновременно
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) и
Get-Content -LiteralPath <path> -Encoding UTF8; для записи используй явный UTF-8.
Не перекодируй исходники в CP1251/OEM. Читай независимые входы одним вызовом,
большие JSON разбирай программно и выбирай нужные поля. После проверки сохрани
требуемые артефакты; не запускай runner или новый writer/validator из этого места.
Если для записи нужен вспомогательный скрипт или временный файл, создай его
внутри текущего рабочего каталога задания. Системный TEMP находится за его границами.
"""

WRITER_PROMPT = """Ты — writer-агент скилла wiki-doc. Следуй инструкциям пакета:
- {skill}/SKILL.md (рабочий процесс), {skill}/doc-writer.md (правила writer),
  {skill}/template.md (карта секций), {skill}/references/facts.md (контракт реестра).

Вход: SQL {project}/{sql} (диалект {dialect}, версия {version}); документируемый
субъект {subject}; контекст DDL: {context}; порядок миграций: {migrations}.
Конечный каталог wiki: {wiki}. Страница будет опубликована в этом каталоге
под page_id из validation_plan.json. Относительная ссылка на исходный SQL: {sql_link}.
Строй ссылки от конечного пути страницы; простое имя файла в backticks ссылку не заменяет.

Уже подготовлено в каталоге {run} и НЕ изменяется тобой: facts.json,
inventory.json, validation_plan.json. Используй их для точных ID, счётчиков,
типов, выражений и зависимостей и сверяй с исходными SQL/DDL. Реестр не заменяет
исходник и семантику установленного языка: отсутствие отдельного fact_id для
следствия не делает это следствие неизвестным. Раскрывай ветви и результат по
коду, указывая основание; не выдумывай значения и не исполняй SQL.

Запиши два файла в {run}:
1. page.draft.md — существующий черновик состоит из служебных таблиц claims.
   Строки `| Fact | Property | SQL value |` вместе с JSON-значениями, маркеры
   `<!-- wiki-doc:fragment … -->` и идентификаторы секций `{{#…}}` — механический
   контракт проверки (claims-v1/coverage): их содержимое и количество сохраняются
   без изменений. Весь остальной текст перепиши как читаемую документацию по
   template.md и doc-writer.md: назначение объекта, сигнатуру, сущности, формулы
   и условия с привязкой к fact_id, поток данных reads → operation → writes
   (отдельно calls), ограничения и явные неизвестные. Подтверждённое отделяй от
   гипотез; неизвестное не переноси как установленный факт. Дополнительные
   гарантии о результате, ошибках, транзакциях или оптимизаторе допустимы только
   с проверенным основанием и точной областью действия; не добавляй их для объёма.
   В разделе неопределённостей оставляй только действительно недостающие данные.
   Для каждого unknown проверь, нельзя ли получить ответ из SQL/DDL и правил
   языка. Не объявляй известную ветку, присваивание или результат неизвестными
   из-за отсутствия отдельной записи реестра. BEGIN/END тела PL/pgSQL обозначают
   блок, а не начало/конец транзакции; отсутствие COMMIT/ROLLBACK не означает
   отсутствие BEGIN и не отменяет транзакционные правила функции.
2. coverage.json — сохрани schema_version/run_id/page_id, обнови entries так,
   чтобы каждый fact_id из facts.json имел запись с section_id реально
   существующего раздела страницы (схема: {skill}/schemas/coverage.schema.json).

Не изменяй facts.json, inventory.json и validation_plan.json; не используй и не
запрашивай внешние ожидания. Единственный источник инструкций и скриптов —
каталог {skill}; другие копии скилла, подгруженные системой, не используй.
Рабочий процесс без лишней разведки: прочитай исходный SQL, нужные DDL/миграции,
перечисленные выше файлы пакета и артефакты каталога {run}, затем запиши два файла; поиск по файловой
системе не нужен. В конце ответа коротко перечисли написанные разделы и вопросы,
которые оставил как явно неизвестные."""

VALIDATOR_PROMPT = """Ты — validator-агент скилла wiki-doc. Прочитай {skill}/doc-validator.md
и следуй его порядку проверки: сначала независимая сверка SQL → реестр, затем
SQL/DDL + реестр → страница. Файлы не исправляй.

Проверь {run}/page.draft.md против SQL {project}/{sql} (контекст {context},
миграции {migrations}) и артефактов {run}: facts.json, inventory.json,
coverage.json, validation_plan.json.
Конечный каталог страницы: {wiki}; исходный SQL доступен по относительной
ссылке {sql_link}. Ссылки проверяются относительно конечного пути страницы.

{run}/validation.json содержит каркас всех {total} обязательств validation_plan.json
со статусом inconclusive: содержательная проверка ещё не выполнена. Сохрани
id = "result:" + <id обязательства>, plan_check_id и все обязательные строки.
Для каждого пункта установи собственный статус, причину, fact_ids и доказательства.
Нельзя массово объявить пункты ok только потому, что механические claims совпадают.

Отдельно прочитай ВСЮ видимую авторскую прозу: {run}/page.prose.txt содержит
её без повторяющихся служебных таблиц; номера L относятся к page.draft.md.
Проверь также заголовки, подписи, обычные таблицы, примеры и Mermaid. Для каждого
технического утверждения ищи подтверждение в исходном SQL/DDL, а не в соседнем
правильном claims-блоке. Отрицание операции, ограничения результата, условия
ошибки и гарантии выполнения требуют такого же доказательства, как формулы.
Проверяй истинность самих неопределённостей: отсутствие отдельного факта не
отменяет вывода из SQL/DDL и семантики языка. Проследи guards, присваивания и
RETURN для TRUE/FALSE/NULL, если они влияют на результат. Не принимай unknown,
противоречащий уже описанной ветке. Различай BEGIN/END блока PL/pgSQL и команды
управления транзакцией; отсутствие последних не является отсутствием блока.
Проверяй также каждую альтернативу в скобках и перечислениях. Совет о способе
вызова проверяется даже без полного SQL-примера: CALL вызывает процедуру,
функция вызывается через SELECT либо PERFORM внутри PL/pgSQL. Отсутствие
аргументов не делает эти формы взаимозаменяемыми. Отличай рекомендацию от
правильного отрицания или сравнения; неполная запись не освобождает от проверки.
В validation-review.md перечисли проверенные диапазоны прозы, утверждения и
конкретные основания или контрпримеры. Если часть не проверена, сохрани
registry_document как inconclusive.

Найденный дефект свободной прозы добавляй отдельной строкой с уникальным id
review:<номер>, category=technical, blocking=true и предметным defect_code;
plan_check_id для дополнительной строки не требуется. Корректная таблица claims
не компенсирует неверное пояснение. Не относить технические ошибки к editorial.
Дополнительные ok не нужны. Evidence — только структурированные ссылки
root/path/start_line/end_line/sha256 на исходники и точные строки page.draft.md;
page.prose.txt является навигацией, не источником evidence.
Схема: {skill}/schemas/validation.schema.json.
**Сначала запиши содержательное заключение в {run}/validation-review.md**,
затем обязательно обнови validation.json. Другие файлы не изменяй. Единственный источник
инструкций — каталог {skill}; другие копии скилла, подгруженные системой, не
используй. Работай без лишней разведки. В конце ответа перечисли найденные дефекты."""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def call_agent(argv_template, *, message, cwd, model, timeout, log_path):
    # Windows argv handling truncates multi-line arguments at the first line
    # break, so the task text is flattened; the full prompt is also persisted.
    full_message = message
    message = ' '.join(message.split())
    command = [part.replace('{message}', message).replace('{cwd}', str(cwd))
               .replace('{model}', model) for part in argv_template]
    resolved = shutil.which(command[0])
    if resolved:
        command[0] = resolved
    # npm's Windows .cmd launcher has a much smaller limit than CreateProcess.
    # Preserve the complete task in UTF-8 instead of truncating repair feedback.
    if (sys.platform == 'win32' and Path(command[0]).suffix.lower() in ('.cmd', '.bat')
            and len(subprocess.list2cmdline(command)) > 7000):
        task_path = log_path.with_suffix('.task.md').resolve()
        task_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.write_text(full_message, encoding='utf-8')
        message = f'Read the UTF-8 task file and carry out its instructions. TASK_FILE: {task_path}'
        command = [part.replace('{message}', message).replace('{cwd}', str(cwd))
                   .replace('{model}', model) for part in argv_template]
        command[0] = resolved or command[0]
    stderr_path = log_path.with_suffix('.stderr.log')
    returncode, seconds = run_logged(command, cwd=cwd, timeout=timeout,
                                     stdout_path=log_path, stderr_path=stderr_path)
    # Preserve the historical combined-log format; stdout is already visible
    # while the process is running and stderr has its own live file.
    with log_path.open('ab') as output:
        output.write(b'\n--- stderr ---\n' + stderr_path.read_bytes())
    return returncode, seconds, [str(c) for c in command]


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
    result when `artifact` or the optional `alt_artifact` changed (a new or
    updated review confirms a draft that stays unchanged). Returns
    (ok, seconds, command, continuations).
    """
    total, continuations, template = 0.0, 0, list(agent)
    command = []
    for attempt in range(rounds + 1):
        before = sha256_bytes(artifact.read_bytes()) if artifact.is_file() else None
        alt_before = (sha256_bytes(alt_artifact.read_bytes())
                      if alt_artifact is not None and alt_artifact.is_file() else None)
        attempt_log = (log_path if attempt == 0 else log_path.with_name(
            f'{log_path.stem}-continue-{attempt}{log_path.suffix}'))
        code, seconds, command = call_agent(
            template, message=message if attempt == 0 else resume_message,
            cwd=workspace, model=model, timeout=timeout, log_path=attempt_log)
        total += seconds
        if not artifact.is_file():
            return False, round(total, 3), command, continuations
        # The artifact is the seat's contract: delivered work counts even when
        # the agent CLI exits non-zero after finishing it.
        alt_delivered = (alt_artifact is not None and alt_artifact.is_file()
                         and (alt_before is None
                              or sha256_bytes(alt_artifact.read_bytes()) != alt_before))
        if sha256_bytes(artifact.read_bytes()) != before or alt_delivered:
            return True, round(total, 3), command, continuations
        if code:
            return False, round(total, 3), command, continuations
        session, reason = last_session_and_reason(attempt_log)
        if reason != 'length' or not session:
            return False, round(total, 3), command, continuations
        template = continue_argv(template, session)
        continuations += 1
    return False, round(total, 3), command, continuations


def run_wiki_root(run_dir):
    """Stable final page base shared by isolated generation and publication."""
    run_dir = Path(run_dir).resolve()
    return run_dir.parent.parent / 'wiki' / run_dir.name


def build_substrate(request, subject, run_dir):
    from build_bundle import build
    project = Path(request['project_root'])
    source = project / request['sql']
    context = [project / p for p in request.get('context') or ()]
    migration = project / request['migration_manifest'] if request.get('migration_manifest') else None
    result = build(source, run_dir, project_root=project, subject=subject,
                   context=context, version=request['version'],
                   migration_manifest=migration,
                   wiki_root=run_wiki_root(run_dir),
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
    data = read_json(validation)
    rebound = 0
    files = {}
    for check in data.get('checks', []):
        for entry in check.get('evidence', []):
            if not isinstance(entry, dict) or entry.get('root') != 'run':
                continue
            target = run_dir / entry.get('path', '')
            if not target.is_file():
                continue
            if target not in files:
                raw = target.read_bytes()
                files[target] = (sha256_bytes(raw), len(raw.decode('utf-8-sig').splitlines()))
            digest, lines = files[target]
            # Preserve the validator's precise range when it already refers to
            # these bytes. Invalid current ranges must be rejected by the gate.
            if entry.get('sha256') != digest:
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
    if result.get('binding'):
        verify_run_binding(skill_root, request, run_dir, result['binding'])
    rebind_run_evidence(run_dir)
    facts = read_json(run_dir / 'facts.json')
    plan = read_json(run_dir / 'validation_plan.json')
    source = project / request['sql']
    context = [project / p for p in request.get('context') or ()]
    migration = project / request['migration_manifest'] if request.get('migration_manifest') else None
    manifest = create_manifest(
        run_id=facts['run_id'], page_id=plan['page_id'], sql_files=[source],
        context_files=context, migration_manifest=migration, artifacts_dir=run_dir,
        project_dir=project,
        tool_versions=compute_tool_versions(skill_root, profile_path=profile))
    write_manifest(manifest, run_dir / 'manifest.json')
    checked = evaluate_bundle(run_dir, roots={'project': project, 'wiki': run_wiki_root(run_dir)},
                              profile_path=profile, write_decision=True)
    if result.get('binding'):
        try:
            verify_run_binding(skill_root, request, run_dir, result['binding'])
        except (ValueError, OSError):
            (run_dir / 'decision.json').unlink(missing_ok=True)
            raise
    return checked


def pin_run_binding(skill_root, request, run_dir, result):
    """Keep the pre-writer identity, runtime and immutable input bytes in memory."""
    manifest = read_json(run_dir / 'manifest.json')
    bound = {key: manifest[key] for key in (
        'run_id', 'page_id', 'tool_versions', 'sql_files', 'context_files',
        'documented_subjects', 'migration_manifest') if key in manifest}
    bound['artifacts'] = {name: manifest['artifacts'][name]
                          for name in ('facts', 'inventory', 'validation_plan')}
    binding = dict(manifest=bound, skill_root=str(Path(skill_root).resolve()),
                   project_root=str(Path(request['project_root']).resolve()),
                   request=json.loads(json.dumps(request)), profile=result.get('profile'))
    verify_run_binding(skill_root, request, run_dir, binding)
    return binding


def preparation_binding(skill_root, request, run_dir):
    """Pin inputs and loaded runtime before analysis can observe changing files."""
    from bundle import create_manifest, compute_tool_versions
    from profiles import CKR
    project = Path(request['project_root'])
    profile = Path(request['profile']) if request.get('profile') else CKR
    versions = compute_tool_versions(Path(skill_root), profile_path=profile)
    loaded = compute_tool_versions(Path(__file__).resolve().parent.parent, profile_path=profile)
    if loaded != versions:
        raise ValueError('Requested skill runtime differs from the executing adapter')
    before = create_manifest(run_id='preparing', page_id='preparing',
        sql_files=[project / request['sql']], context_files=[project / p for p in request.get('context') or ()],
        migration_manifest=project / request['migration_manifest'] if request.get('migration_manifest') else None,
        artifacts_dir=run_dir, project_dir=project, tool_versions=versions)
    return before


def verify_run_binding(skill_root, request, run_dir, binding):
    from bundle import compute_tool_versions, verify_manifest_hashes
    if (str(Path(skill_root).resolve()) != binding['skill_root']
            or str(Path(request['project_root']).resolve()) != binding['project_root']
            or request != binding['request']):
        raise ValueError('Adapter request or roots changed after substrate preparation')
    profile = Path(binding['profile']) if binding.get('profile') else None
    if compute_tool_versions(Path(skill_root), profile_path=profile) != binding['manifest']['tool_versions']:
        raise ValueError('Adapter runtime changed after substrate preparation')
    errors = verify_manifest_hashes(binding['manifest'], run_dir,
                                   roots={'project': Path(binding['project_root'])})
    if errors:
        raise ValueError('Adapter pinned input/identity changed: ' + '; '.join(errors))


RUN_ARTIFACTS = ('page.draft.md', 'coverage.json', 'validation.json', 'validation-review.md',
                 'page.prose.txt',
                 'facts.json', 'inventory.json', 'validation_plan.json', 'manifest.json',
                 'decision.json')


WRITER_REPAIR_NOTE = 'Ремонт (попытка {attempt} из {rounds}) после отказа гате: предыдущий результат получил перечисленные замечания. Устрани их в page.draft.md и coverage.json, сохранив механический контракт claims-v1: служебные таблицы `| Fact | Property | SQL value |`, маркеры `<!-- wiki-doc:fragment ... -->` и идентификаторы секций не изменяй. facts.json, inventory.json и validation_plan.json не изменяй.\n\nЗамечания предыдущей попытки:'
VALIDATOR_REPAIR_NOTE = 'Повторная проверка после ремонта (попытка {attempt} из {rounds}): страница исправлялась по перечисленным замечаниям. Провер её текущее состояние заново и обнови validation.json и validation-review.md: замечания могли быть устранены или остаться. Механический контракт не изменяй.\n\nЗамечания предыдущей попытки:'


def repair_note(template, attempt, rounds, feedback):
    return template.format(attempt=attempt, rounds=rounds) + '\n' + feedback


def seat_record(role, round_number, delivered, seconds, command, continuations):
    return dict(role=role, round=round_number, delivered=delivered, seconds=seconds,
                command=command, continuations=continuations)


def snapshot_run(run_dir):
    return {name: (run_dir / name).read_bytes() for name in RUN_ARTIFACTS
            if (run_dir / name).is_file()}


def restore_run(run_dir, snapshot):
    for name in RUN_ARTIFACTS:
        path = run_dir / name
        if name in snapshot:
            path.write_bytes(snapshot[name])
        elif path.is_file():
            path.unlink()


def repair_feedback(run_dir, gate):
    parts = ['- ' + str(error) for error in (gate.get('errors') or ())[:30]]
    validation = run_dir / 'validation.json'
    if validation.is_file():
        try:
            data = read_json(validation)
        except ValueError:
            data = {}
        for check in (data.get('checks') or ()):
            if check.get('status') != 'ok':
                parts.append('- {0} [{1}] {2}'.format(
                    check.get('id'), check.get('status'), check.get('reason')))
    review = run_dir / 'validation-review.md'
    if review.is_file():
        text = review.read_text(encoding='utf-8-sig', errors='replace')
        if text.strip():
            parts.append(text[:4000])
    return '\n'.join(parts[:60]).strip()


def prompt_for(template, skill_root, request, run_dir, *, subject, **extra):
    project = Path(request['project_root'])
    wiki = run_wiki_root(run_dir)
    sql_link = quote(os.path.relpath(project / request['sql'], wiki).replace('\\', '/'), safe='/')
    return template.format(
        skill=skill_root, run=run_dir, project=project, sql=request['sql'],
        dialect=request.get('dialect', 'postgres'), version=request['version'],
        subject=subject, wiki=wiki, sql_link=sql_link,
        context=', '.join(request.get('context') or ()) or 'нет',
        migrations=request.get('migration_manifest') or 'нет', **extra) + FILE_ACCESS_NOTE


def prepare_semantic_review(run_dir):
    """Make unreviewed checks explicit and expose prose apart from claims tables.

    The projection is navigation only: evidence still binds the original draft.
    Neither a projection nor a mechanical pass supplies semantic approval.
    """
    from coverage_gate import MarkdownIt, MARKER
    from page_claims import expected_claims
    from wiki_store import atomic_json
    run_dir = Path(run_dir)
    page = run_dir / 'page.draft.md'
    raw = page.read_bytes()
    text = raw.decode('utf-8-sig')
    lines = text.splitlines()
    tokens = MarkdownIt('commonmark').enable(['table', 'strikethrough']).parse(text)
    expected = expected_claims(read_json(run_dir / 'facts.json'))
    omitted = set()
    for index, token in enumerate(tokens):
        if token.type != 'table_open' or not token.map:
            continue
        headers, rows, row, cell_text = [], [], None, None
        in_header, simple_cells = False, True
        for cell_index in range(index + 1, len(tokens)):
            cell = tokens[cell_index]
            if cell.type == 'table_close':
                break
            if cell.type == 'thead_open':
                in_header = True
            elif cell.type == 'thead_close':
                in_header = False
            elif cell.type == 'tr_open':
                row = []
            elif cell.type in ('td_open', 'th_open'):
                cell_text = ''
            elif cell.type == 'inline' and cell_text is not None:
                simple_cells = simple_cells and all(
                    child.type in ('text', 'code_inline') and not child.attrs
                    for child in cell.children or ())
                cell_text += ''.join(child.content for child in cell.children or ()
                                     if child.type in ('text', 'code_inline'))
            elif cell.type in ('td_close', 'th_close'):
                row.append(cell_text)
                cell_text = None
            elif cell.type == 'tr_close':
                if in_header:
                    headers = row
                else:
                    rows.append(row)
        known = bool(rows) and simple_cells and headers == ['Fact', 'Property', 'SQL value']
        for row in rows:
            if not known:
                break
            try:
                known = (len(row) == 3 and (row[0], row[1]) in expected
                         and json.loads(row[2]) == expected[(row[0], row[1])])
            except ValueError:
                known = False
        if known:
            omitted.update(range(*token.map))
    projected = [f'Source: page.draft.md; SHA-256: {sha256_bytes(raw)}',
                 'L numbers refer to original draft lines. Claims tables are omitted; review the original for evidence.']
    for number, line in enumerate(lines):
        if number in omitted or not line.strip() or MARKER.fullmatch(line):
            continue
        projected.append(f'L{number + 1:06d} | {line}')
    (run_dir / 'page.prose.txt').write_text('\n'.join(projected) + '\n', encoding='utf-8')
    validation = read_json(run_dir / 'validation.json')
    for check in validation['checks']:
        check['status'] = 'inconclusive'
        check['reason'] = 'Independent semantic review of the current draft is pending.'
        check.pop('defect_code', None)
    atomic_json(run_dir / 'validation.json', validation)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request')
    parser.add_argument('--agent', default=json.dumps(DEFAULT_AGENT),
                        help='JSON argv; {message}/{cwd}/{model} placeholders are replaced')
    parser.add_argument('--writer-agent', help='Optional writer JSON argv; defaults to --agent')
    parser.add_argument('--validator-agent', help='Optional independent validator JSON argv; defaults to --agent')
    parser.add_argument('--model', required=True)
    parser.add_argument('--validator-model', help='Validator model label; defaults to --model')
    parser.add_argument('--llm-timeout', type=int, default=900)
    parser.add_argument('--repair-rounds', type=int, default=0,
                        help='Repair rounds after a refused seal (0..3)')
    args = parser.parse_args(argv)
    if args.llm_timeout <= 0:
        parser.error('LLM timeout must be positive')
    if not 0 <= args.repair_rounds <= 3:
        parser.error('Repair rounds must be between 0 and 3')
    request = read_json(Path(args.request))
    def agent_argv(value):
        result = json.loads(value)
        if not isinstance(result, list) or not result or not all(isinstance(p, str) for p in result):
            raise SystemExit('agent argv must be a non-empty JSON array of strings')
        return result
    writer_agent = agent_argv(args.writer_agent or args.agent)
    validator_agent = agent_argv(args.validator_agent or args.agent)
    validator_model = args.validator_model or args.model
    skill_root = Path(request['skill_root']).resolve()
    skill_scripts = skill_root / 'scripts'
    sys.path.insert(0, str(skill_scripts))
    output = Path(request['output_dir']).resolve()
    workspace = output.parent
    runs, failures = [], []
    for index, subject in enumerate(request['subjects']):
        run_dir = output / str(index)
        started = time.monotonic()
        try:
            before = preparation_binding(skill_root, request, run_dir)
            substrate = build_substrate(request, subject, run_dir)
        except (ValueError, OSError, KeyError) as exc:
            (run_dir / 'decision.json').unlink(missing_ok=True)
            failures.append(f'{subject}: preparation refused: {exc}')
            continue
        # A substrate decision is not a completed authoring/validation cycle.
        (run_dir / 'decision.json').unlink(missing_ok=True)
        binding_failure = None
        log_dir = workspace / 'agent-logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            substrate['binding'] = pin_run_binding(skill_root, request, run_dir, substrate)
            pinned = substrate['binding']['manifest']
            for field in ('sql_files', 'context_files', 'migration_manifest'):
                if pinned.get(field) != before.get(field):
                    raise ValueError(f'Adapter {field} changed during preparation')
            if any(before['tool_versions'].get(key) != value
                   for key, value in pinned['tool_versions'].items()):
                raise ValueError('Adapter runtime changed during preparation')
        except (ValueError, OSError, KeyError) as exc:
            failures.append(f'{subject}: cannot bind prepared run: {exc}')
            continue
        (log_dir / f'{index}-binding.json').write_text(
            json.dumps(substrate['binding'], ensure_ascii=False, indent=2), encoding='utf-8')

        def bound_seat(*args, **kwargs):
            nonlocal binding_failure
            delivered = (False, 0, [], 0)
            try:
                verify_run_binding(skill_root, request, run_dir, substrate['binding'])
                delivered = seat(*args, **kwargs)
                verify_run_binding(skill_root, request, run_dir, substrate['binding'])
                return delivered
            except (ValueError, OSError, KeyError) as exc:
                binding_failure = str(exc)
                (run_dir / 'decision.json').unlink(missing_ok=True)
                (log_dir / f'{index}-binding-failure.json').write_text(json.dumps(dict(
                    decision='blocked', publication_authorized=False,
                    error=binding_failure, seat=kwargs['log_path'].name),
                    ensure_ascii=False, indent=2), encoding='utf-8')
                return False, delivered[1], delivered[2], delivered[3]

        page = run_dir / 'page.draft.md'
        writer_prompt = prompt_for(WRITER_PROMPT, skill_root, request, run_dir, subject=subject)
        prompt_file = workspace / 'agent-logs' / f'{index}-writer-prompt.md'
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(writer_prompt, encoding='utf-8')
        writer_ok, writer_seconds, writer_command, writer_continuations = bound_seat(
            writer_agent, message=f'Полный текст задания сохранён в файле {prompt_file}. {writer_prompt}',
            resume_message='Продолжай задание этой сессии: страница ещё не записана. '
                           'Запиши page.draft.md и coverage.json по заданию выше.',
            workspace=workspace, model=args.model, timeout=args.llm_timeout,
            log_path=workspace / 'agent-logs' / f'{index}-writer.log', artifact=page,
            alt_artifact=run_dir / 'coverage.json')
        if not writer_ok:
            failures.append(f'{subject}: no writer result for page.draft.md '
                            f'(continuations: {writer_continuations})')
            continue
        validation = run_dir / 'validation.json'
        prepare_semantic_review(run_dir)
        validator_prompt = prompt_for(VALIDATOR_PROMPT, skill_root, request, run_dir,
                                      subject=subject,
                                      total=len(read_json(run_dir / 'validation_plan.json')['required_checks']))
        validator_prompt_file = workspace / 'agent-logs' / f'{index}-validator-prompt.md'
        validator_prompt_file.write_text(validator_prompt, encoding='utf-8')
        validator_ok, validator_seconds, validator_command, validator_continuations = bound_seat(
            validator_agent, message=f'Полный текст задания сохранён в файле {validator_prompt_file}. {validator_prompt}',
            resume_message='Продолжай задание этой сессии: отчёт ещё не записан. '
                           'Запиши validation.json по заданию выше.',
            workspace=workspace, model=validator_model, timeout=args.llm_timeout,
            log_path=workspace / 'agent-logs' / f'{index}-validator.log', artifact=validation,
            alt_artifact=run_dir / 'validation-review.md')
        if not validator_ok:
            failures.append(f'{subject}: no validator result for validation.json '
                            f'(continuations: {validator_continuations})')
            continue
        try:
            gate = reseal(skill_root, request, run_dir, substrate)
        except (ValueError, OSError, KeyError) as exc:
            (run_dir / 'decision.json').unlink(missing_ok=True)
            failures.append(f'{subject}: sealing refused: {exc}')
            continue
        initial_decision = gate['decision']
        first_writer = dict(command=writer_command, seconds=writer_seconds,
                            continuations=writer_continuations, page_sha256=sha256_bytes(page.read_bytes()))
        first_validator = dict(command=validator_command, seconds=validator_seconds,
                               continuations=validator_continuations,
                               validation_sha256=sha256_bytes(validation.read_bytes()))
        seat_attempts = [
            seat_record('writer', 0, True, writer_seconds, writer_command, writer_continuations),
            seat_record('validator', 0, True, validator_seconds, validator_command, validator_continuations)]
        repair_history, repair_rounds_done = [], 0
        while (repair_rounds_done < args.repair_rounds
               and not (gate['decision'] == 'ready' and gate['publication_authorized'])):
            attempt = repair_rounds_done + 1
            feedback = repair_feedback(run_dir, gate)
            if not feedback:
                repair_history.append(dict(round=attempt, outcome='no_actionable_feedback'))
                break
            snapshot = snapshot_run(run_dir)
            repair_rounds_done += 1
            repair_writer_prompt = writer_prompt + repair_note(
                WRITER_REPAIR_NOTE, attempt, args.repair_rounds, feedback)
            repair_writer_file = (workspace / 'agent-logs'
                                  / f'{index}-writer-repair-{attempt}-prompt.md')
            repair_writer_file.write_text(repair_writer_prompt, encoding='utf-8')
            writer_ok, writer_seconds, writer_command, writer_continuations = bound_seat(
                writer_agent, message=f'Полный текст задания сохранён в файле {repair_writer_file}. {repair_writer_prompt}',
                resume_message='Продолжай ремонт текущей сессии: страница ещё не записана. Запиши page.draft.md и coverage.json по заданию выше.',
                workspace=workspace, model=args.model, timeout=args.llm_timeout,
                log_path=workspace / 'agent-logs' / f'{index}-writer-repair-{attempt}.log',
                artifact=page, alt_artifact=run_dir / 'coverage.json')
            seat_attempts.append(seat_record('writer', attempt, writer_ok, writer_seconds,
                                             writer_command, writer_continuations))
            if not writer_ok:
                restore_run(run_dir, snapshot)
                repair_history.append(dict(round=attempt, outcome='writer_failed'))
                break
            prepare_semantic_review(run_dir)
            repair_validator_prompt = validator_prompt + repair_note(
                VALIDATOR_REPAIR_NOTE, attempt, args.repair_rounds, feedback)
            repair_validator_file = (workspace / 'agent-logs'
                                    / f'{index}-validator-repair-{attempt}-prompt.md')
            repair_validator_file.write_text(repair_validator_prompt, encoding='utf-8')
            validator_ok, validator_seconds, validator_command, validator_continuations = bound_seat(
                validator_agent, message=f'Полный текст задания сохранён в файле {repair_validator_file}. {repair_validator_prompt}',
                resume_message='Продолжай повторную проверку текущей сессии: отчёт ещё не обновлён. Запиши validation.json по заданию выше.',
                workspace=workspace, model=validator_model, timeout=args.llm_timeout,
                log_path=workspace / 'agent-logs' / f'{index}-validator-repair-{attempt}.log',
                artifact=validation, alt_artifact=run_dir / 'validation-review.md')
            seat_attempts.append(seat_record('validator', attempt, validator_ok, validator_seconds,
                                             validator_command, validator_continuations))
            if not validator_ok:
                restore_run(run_dir, snapshot)
                repair_history.append(dict(round=attempt, outcome='validator_failed'))
                break
            try:
                repaired_gate = reseal(skill_root, request, run_dir, substrate)
            except (ValueError, OSError, KeyError, TypeError) as exc:
                restore_run(run_dir, snapshot)
                repair_history.append(dict(round=attempt, outcome='seal_failed', errors=[str(exc)]))
                break
            gate = repaired_gate
            repair_history.append(dict(round=attempt, outcome='sealed',
                                       decision=gate['decision'],
                                       errors=list(gate['errors'])))
        if binding_failure:
            try:
                # A failed repair may have restored the prior refused bundle.
                # Keep that decision only while its original binding still holds.
                verify_run_binding(skill_root, request, run_dir, substrate['binding'])
            except (ValueError, OSError, KeyError):
                (run_dir / 'decision.json').unlink(missing_ok=True)
                gate = dict(decision='blocked', publication_authorized=False, errors=[binding_failure])
        (workspace / 'agent-logs' / f'{index}-adapter.json').write_text(json.dumps({
            'subject': subject, 'model': args.model,
            'models': {'writer': args.model, 'validator': validator_model},
            'writer': first_writer,
            'validator': first_validator,
            'seat_attempts': seat_attempts,
            'final_page_sha256': sha256_bytes(page.read_bytes()),
            'final_validation_sha256': sha256_bytes(validation.read_bytes()),
            'initial_decision': initial_decision,
            'repair_rounds': repair_rounds_done,
            'repair_history': repair_history,
            'note': 'Seat continuations retry the same generation attempt on output-cap '
                    'stops; repair rounds re-run both seats with the gate feedback '
                    'and count only actually executed attempts.',
            'gate': {'decision': gate['decision'], 'publication_authorized': gate['publication_authorized'],
                     'errors': gate['errors']},
            'total_seconds': round(time.monotonic() - started, 3),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        runs.append(dict(subject=subject, run_dir=str(index),
                         profile=substrate.get('profile'),
                         initial_decision=initial_decision,
                         repair_rounds=repair_rounds_done))
    (output / 'runs.json').write_text(json.dumps({'schema_version': 1, 'runs': runs},
                                                 ensure_ascii=False), encoding='utf-8')
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
