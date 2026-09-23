# Статус доработок wiki-doc — 2026-09-23

План: [IMPROVEMENT-PLAN-2026-09-23.md](IMPROVEMENT-PLAN-2026-09-23.md).
Этот файл — журнал выполнения задач Q-01…Q-09. История предыдущего этапа находится
в [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md).

**Текущий итог:** Q-02 и Q-03 выполнены. Q-03 — ограниченный адаптер Greenplum:
`EXECUTE ON MASTER/ANY/ALL SEGMENTS` стал атрибутом объявления, `DISTRIBUTED BY/RANDOMLY/
REPLICATED` и параметры `WITH` разбираются в инвентаре и DDL-каталоге; малые
положительные GP-примеры и контрольный q05 проходят анализ и полный gate;
подмена/удаление MASTER и DISTRIBUTED обнаруживаются gate; неизвестные
GP-расширения блокируются с локализованной причиной. Q-01 остаётся `in_progress`
(незакрытая приёмка — комплект проверок ошибок старой сохранённой страницы
`test-sql-wiki`). Q-04…Q-09 не начаты. Следующее действие — Q-04 (полный анализ
тела, зависимостей и DDL до контрольного объекта).

Повторное ревью выявило и исправило ложные ready, потери GP-атрибутов,
ошибку Unicode-смещений, контроль версии и пробелы контракта/инструкций:
[REVIEW-Q03.md](REVIEW-Q03.md), [Q03-REVIEW.json](Q03-REVIEW.json).
Итог: **835 tests (834 passed, 1 skipped), 0 failed**; все 24 модуля unittest
в отдельных процессах, четыре процесса одновременно, 140.609s.
Q-03 — 30 исходных + 35 тестов повторного ревью; Q-01 — 30; Q-02 — 30.
Q-набор — 125 тестов. Reference/saved: по 45/45 результатов
(15 объектов × 3 повтора); q05: 3/3 изолированных reference-gate при `unknown`.
**Настоящий LLM-прогон не выполнялся** (Q-08).

Предыдущая итерация (история): результат 764 tests / 1 skipped относится к Q-01/Q-02.
Повторная проверка Codex выявила и исправила ошибки Q-фикстур, диагностик и
тестов; подробности — [REVIEW-Q01-Q02.md](REVIEW-Q01-Q02.md).
Команды, версии, хеши и результаты — [Q01-Q02-REVIEW.json](Q01-Q02-REVIEW.json).

## Как обновлять файл

- После существенного шага обновляй сводку и подробный блок задачи: дату, исполнителя,
  сделанные изменения, результаты проверок и следующее действие.
- Статусы: `planned` — не начато; `in_progress` — выполняется; `blocked` — есть
  конкретное препятствие; `done` — выполнены все критерии приёмки из плана.
- В «Выполнено» перечисляй только фактические результаты и изменённые файлы.
  Невыполненное записывай в приёмку или следующее действие.
- Для проверок указывай команду, рабочий каталог, результат и ссылку на лог/отчёт,
  если он сохранён. Если проверка не запускалась — так и пиши. Разделяй unit-тесты,
  reference/saved и настоящий LLM-прогон; не переноси прежние числа как новые результаты.
- Не ставь `done` по наличию кода или успешным unit-тестам, если остались критерии
  приёмки. Для блокировки укажи, чего не хватает и что требуется для продолжения.

## Сводка задач

| Задача | Результат по плану | Статус |
|---|---|---|
| Q-01 | Независимые ожидания и воспроизведение дефектов | in_progress |
| Q-02 | Диагностика вместо аварии fallback | done |
| Q-03 | Поддержка необходимого поднабора Greenplum | done |
| Q-04 | Анализ тела функции, зависимостей и DDL | planned |
| Q-05 | Проверяемый запуск и происхождение результата | planned |
| Q-06 | Генерация больших страниц без потери логики | planned |
| Q-07 | Независимая проверка содержания и мутации | planned |
| Q-08 | Настоящий LLM-цикл и приёмка на реальном SQL | planned |
| Q-09 | Документация поддержки и итоговый статус | planned |

## Подробности выполнения

### Q-01. Зафиксировать независимые ожидания

**Статус:** in_progress.
**Обновлено:** 2026-09-23.
**Исполнитель:** opencode (`xiaomi-token-plan-sgp/mimo-v2.6-pro`).

**Повторная проверка и исправления:** Codex, 2026-09-23.

**Выполнено:**

- Компактные SQL-фикстуры `wiki-doc/wiki-doc/examples/fixtures/`: 11 случаев
  (q01…q11) + 2 DDL-контекста (`q_context.sql` с читаемыми и изменяемыми
  объектами, `q_context_gp.sql` с `DISTRIBUTED BY`/`WITH`) + миграция
  `migrations/{manifest.json, baseline_orders.sql, 014_amount_reorder.sql}` +
  README с правилами изоляции.
- Четыре ошибки P1 воспроизводятся отдельными случаями: q01 (ретро-ветка через
  локальную переменную с исторической парой DELETE/INSERT), q02 (`IS NOT NULL`
  против `IS NULL`), q03 (разные наборы is_done/is_phoned + приоритет
  дедубликации и конкретный двухстрочный контрпример), q04 (ветви до/после фиксированной даты).
- Конструкции большого объекта: q05 (`EXECUTE ON MASTER` после тела + вложенные
  производные таблицы), q06 (вложенные CTE/производные таблицы, одинаковые имена
  CTE в разных INSERT), q07 (XML-агрегат filters), q08 (позиционный INSERT и
  перестановка колонок миграцией).
- KPI/расчёты: q09 (разные уровни и структуры KPI), q10 (нормирование часов и
  выбор плана с приоритетом), q11 (вычисления RR и SL).
- Независимые ожидания `examples/expected/qNN/{facts,checks,decision,page_assertions,assertions}.json`.
  Авторские константы выведены **чтением SQL**, не запуском extractor/writer;
  код авторства — `wiki-doc/wiki-doc/examples/expected/_authoring.py`. Ожидания
  недоступны генератору (закреплено тестом
  `ManifestTests.test_expectations_are_not_reachable_from_adapter_inputs`).
- Матрица D01–D12: 35 утверждений (все blocking), каждая ошибка ревью D01–D12
  покрыта минимум одним утверждением; по каждому случаю есть минимум одна
  размеченная мутация (`page_assertions.json`). Это заготовки для Q-07, а не
  доказательство отказа всех мутаций. D12 о совместимости теперь закреплён
  в q05-A5; неизвестная расшифровка RR — дополнительный контроль Q-07.
- Исправлены q03 (независимые пересекающиеся наборы is_done/is_phoned), q08
  (реальный DROP/ADD для перестановки колонок и сверка реконструкции с oracle),
  q09 (явные KPI 10/20, разные уровни 1,2 / 1 и привязка каждой формулы к ветке).
  Три расчётные ветви q09 не объявляются тремя строками данных.
- Положительные и отрицательные контроли: 10 случаев ожидают `ready`, q05 —
  `blocked`. Тест проверяет состав ожиданий; фактический полный gate Q-набора
  пока не выполнен. Положительные gate-контроли Q-02 проверены отдельно.
- Воспроизведение аварии extractor: команда, код возврата 2, stdout/stderr,
  версия пакета 1.0.0, sha256 входа —
  [history/Q01-REPRODUCTION.md](wiki-doc/history/Q01-REPRODUCTION.md).
- Локальный приёмочный сценарий большого SQL с хешем входа и политикой
  `refuse_to_apply_numbers`: `examples/fixtures/acceptance-large.json`.
- Манифест Q-набора `examples/cases-q.json` намеренно **не** смешан с
  `examples/cases.json`, чтобы исторический набор 01–12 и его прогон
  `run_regression` не регрессировали до Q-08. Это отклонение от буквы списка
  «Изменяемые области» плана зафиксировано здесь как осознанное решение.

**Изменённые файлы реализации/тестов:**

- новые: `examples/fixtures/**` (18 файлов), `examples/cases-q.json`,
  `examples/expected/q01…q11/**` (55 файлов) + `examples/expected/_authoring.py`,
  `tests/test_q01_expectations.py`, `history/Q01-REPRODUCTION.md`
- изменены: `scripts/sql_extract.py`, `scripts/ddl.py` (см. Q-02),
  `tests/test_q02_fallback_diagnostics.py` (новый, см. Q-02)

**Приёмка:**

- [x] Подготовлены компактные SQL-случаи и независимые ожидания по критериям Q-01.
- [x] На исходной реализации воспроизведена ошибка extractor; сохранены команда,
      код возврата, stdout/stderr и версия пакета.
- [x] Зафиксированы ошибки прежней документации (D01–D12 как ожидания/мутации) и
      хеши контрольных входов (`acceptance-large.json`).
- [x] В наборе есть положительные и отрицательные случаи; ожидания недоступны writer.
- [ ] **Выполнен воспроизводимый комплект проверок ошибок старой документации** — нет:
      исходная страница и старый аудит остаются внешними контрольными входами
      (`test-sql-wiki`). Это приёмка Q-01, которую нельзя подменять ожиданиями
      малых фикстур; полный новый LLM-цикл — отдельная приёмка Q-08.

**Проверки предыдущей сессии (история):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 50 passed |
| `python -B -m unittest discover -s tests -v` (полный набор) | wiki-doc/wiki-doc | 757 tests, 1 skipped, 0 failed |

Unit-тесты — да. `reference`/`saved` — не запускались. Настоящий LLM-прогон —
**нет** (Q-08). Граница: тесты сверяют ожидания с SQL механически и проверяют
структурированные утверждения; произвольная поясняющая проза не проверяется.

**Артефакты:** [history/Q01-REPRODUCTION.md](wiki-doc/history/Q01-REPRODUCTION.md),
`examples/fixtures/acceptance-large.json`, `examples/expected/q01…q11/`.

**Ограничения:** подготовка ожиданий сама по себе не подтверждает исправление
генерации. q03-A4 теперь содержит две строки и проверяемые результаты выбора
и флагов; сверка результата writer и исполнение матрицы мутаций остаются Q-07.
Изоляция проверена вызовом `isolate()` для всех 11 случаев и осмотром файлов
workspace; это гарантия переданного комплекта, не песочница доступа процесса.

**Блокировки:** не зафиксированы.

**Следующее действие:** Q-04 — полный анализ тела, зависимостей и DDL до
контрольного объекта (`ckr_uup_db_onboarding.sql`): связь параметр → присваивание →
условие IF → операции ветки, все PL/pgSQL-блоки, области CTE, точные детали
выражений и восстановление DDL по доказанному порядку миграций.

### Q-02. Устранить аварийное завершение анализа

**Статус:** done.
**Обновлено:** 2026-09-23.
**Исполнитель:** opencode (`xiaomi-token-plan-sgp/mimo-v2.6-pro`).
**Повторная проверка и исправления:** Codex, 2026-09-23.

**Выполнено:**

- `scripts/sql_extract.py`: добавлен `_split_at_depth_zero` — граница
  `FROM`/`USING`-хвоста ищется по ключевым словам **вне скобок**, вместо плоского
  `re.split` по `WHERE/GROUP/ORDER/HAVING/RETURNING/WHEN` внутри вложенного
  выражения.
- `scripts/sql_extract.py`: добавлен `_trim_trailing_closers` — срезание только
  несбалансированных закрывающих скобок на хвосте (артефакт depth-blind
  развёртки сегмента), без изменения байтов выражения.
- `scripts/sql_extract.py`: `split_top_level` при проверке перечисленных
  источников и вызовы `extract_operations` в `_extract_inventory_legacy`
  обёрнуты в перехват: вспомогательная ошибка разбора становится блокирующей
  coverage note **с исходным диапазоном**, а не `ValueError` и не пустым
  «успешным» инвентарём.
- `scripts/sql_extract.py`: fallback после отказа AST защищён от любых
  исключений: результирующий инвентарь получает `ANALYSIS_GAP` и две блокирующие
  причины (`PostgreSQL AST analysis failed: …`, `Legacy analysis failed: …`).
  `SubjectSelectionError` (подкласс `ValueError`, ошибка `--subjects`) сохраняет
  код CLI 2. Произвольный `ValueError` вспомогательного разбора — analysis gap.
- `scripts/sql_extract.py`: на пути успешного AST отказ вспомогательного
  legacy-разбора сохраняет нативные операции **и** блокирующую диагностику.
  Выбор субъекта успешным AST имеет приоритет над ограниченным legacy-сканером
  (в частности, для quoted identifiers).
- `scripts/ddl.py`: `catalog` перехватывает отказ парсинга контекстного DDL и
  отдаёт ошибку в список `errors` (становится coverage note `DDL parse failed`),
  а не аварийно завершает CLI. Source ref указывает на контекстный DDL и его
  хеш; заметка сохраняется даже при отсутствии DECLARATION в основном SQL.
  Некорректная кодировка входа остаётся ошибкой CLI 2.

**Изменённые файлы реализации/тестов:** `scripts/sql_extract.py`,
`scripts/ddl.py`, `scripts/sql_ast.py`, `scripts/sql_syntax.py`,
`references/sql-support.md`, новый `tests/test_q02_fallback_diagnostics.py` (30 тестов после Q-03-обзора; в коммите Q-02 было 27),
`tests/test_q01_expectations.py` (CrashReproductionTests).

**Приёмка:**

- [x] q05 **и большой контрольный SQL с совпавшим SHA-256** больше не заканчиваются
      `Unbalanced SQL list`: код 0 и inventory с блокирующими причинами.
      Исходный модуль из HEAD воспроизводит код 2 на обоих входах;
      команды, stdout/stderr и хеши сохранены, см. REVIEW-Q01-Q02.md.
- [x] До реализации GP-поддержки объект остаётся неподдержанным с явной причиной
      (`Unsupported dialect: greenplum` + `PostgreSQL AST analysis failed`).
- [x] Удаление `coverage_notes` из сохранённого файла не обходит повторный анализ
      gate (`validation_gate.py` пере-парсит исходники; тест
      `test_removing_coverage_notes_does_not_unblock_gate`).
- [x] Повреждённый SQL (`CREATE VIEW demo.v AS SELECT FROM;`) не получает ready —
      сохраняются блокирующие coverage notes (`test_broken_sql_does_not_get_ready`).
- [x] Оба пути проверены: отказ AST до fallback (`test_path_a_ast_failure_falls_back_without_crash`,
      `test_path_a_gp_table_context_stays_blocked`) и успешный AST при отказе
      вспомогательного legacy-разбора (`test_path_b_native_success_survives_legacy_failure`);
      ошибка входа не маскируется (`test_path_b_value_error_from_legacy_still_reports_input_errors`,
      `test_malformed_input_keeps_documented_exit_code_2`).
- [x] Некорректный вход отличён от неподдержанной конструкции:
      код 2 для ошибки выбора субъекта/чтения/кодировки входа;
      ошибка вспомогательного разбора → блокирующая coverage note.

**Проверки предыдущей сессии (история):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 50 passed |
| `python -B -m unittest discover -s tests -p "test_sql_ast.py"` | wiki-doc/wiki-doc | 20 passed |
| `python -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 38 passed |
| `python -B -m unittest discover -s tests -p "test_inventory_plan.py"` | wiki-doc/wiki-doc | 22 passed |
| `python -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 passed |
| `python -B -m unittest discover -s tests -p "test_p0_gate_regressions.py"` | wiki-doc/wiki-doc | 36 passed, 1 skipped |
| `python -B -m unittest discover -s tests -p "test_artifact_regressions.py"` | wiki-doc/wiki-doc | 49 passed |
| `python -B -m unittest discover -s tests -p "test_p1_acceptance.py"` | wiki-doc/wiki-doc | 12 passed |
| `python -B -m unittest discover -s tests -v` (полный набор) | wiki-doc/wiki-doc | 757 tests, 1 skipped, 0 failed |

Правила для остальных диалектов не ослаблялись: `test_dialect_matrix.py` проходит
без изменений, включая `test_unsupported_dialect_in_inventory_blocks_gate` и
`test_greenplum_specific_syntax_blocks_analysis`.

**Артефакты:** [history/Q01-REPRODUCTION.md](wiki-doc/history/Q01-REPRODUCTION.md)
(§1 — «до/после», цепочка вызовов, проверяемые утверждения).

**Ограничения:** Q-02 закрывает только аварию. Качество разбора тела (depth-blind
развёртка сегментов, ложные `Unresolved unqualified call: FROM`, переливание
условий через закрывающие скобки) осталось и осознанно отнесено к Q-04. Само
устранение аварии **не** разрешает Greenplum и не разрешает публикацию.

**Блокировки:** не зафиксированы.

**Следующее действие (обновлено после Q-03):** Q-04 — полный анализ тела,
зависимостей и DDL до контрольного объекта.

### Q-03. Поддержать необходимый поднабор Greenplum

**Статус:** done.
**Обновлено:** 2026-09-23.
**Исполнитель:** opencode (`xiaomi-token-plan-sgp/mimo-v2.6-pro`).

**Повторное ревью Q-03:** Codex, 2026-09-23. Статус done подтверждён после
исправлений ниже; исходный отчёт «замечаний нет» не подтвердился.
Подробности, команды и воспроизведения: [REVIEW-Q03.md](REVIEW-Q03.md),
[Q03-REVIEW.json](Q03-REVIEW.json).

- Исправлены перенос атрибутов между объявлениями на одной строке и Unicode:
  символьные смещения pglast переводятся в байтовые диапазоны конкретного RawStmt.
- Проверяются владелец, позиция, границы токенов, дубли EXECUTE ON/DISTRIBUTED,
  идентификаторы BY и порядок WITH. Ошибочные клаузы не удаляются.
- CREATE/CTAS, контекст, миграции и definitions сохраняют GP-атрибуты.
  Противоречащие явные определения и повторы storage parameter блокируются;
  контекст только с колонками не стирает более полное GP-определение.
- Переименование колонки обновляет BY; удаление/смена типа колонки распределения
  возвращает unsupported до реализации анализа перераспределения.
- Прямой/динамический GP MERGE блокируется независимо от GP-версии;
  PostgreSQL сохраняет свой порог >= 15. GP-версия по умолчанию — unknown.
- Добавлена явная `gp_extension_version: 1` и проверка fields в definitions.
  Контракт и инструкции обновлены в текущей задаче, без отсрочки до Q-09.
- Добавлены 35 тестов `test_q03_review.py`, включая мутации claims с актуальными
  хешами evidence/manifest и согласованную подмену facts + inventory + page.
**Выполнено:**

- Новый модуль `scripts/sql_gp.py` — явно включаемый лексический адаптер
  ограниченного GP-поднабора (только при `dialect='greenplum'`; для остальных
  диалектов `prepare()` — тождество входа):
  - `EXECUTE ON {MASTER|ANY|ALL SEGMENTS}` разбирается как **атрибут объявления**
    функции, а не как динамический EXECUTE тела; `GRANT/REVOKE
    EXECUTE ON FUNCTION / ALL FUNCTIONS` не маскируется и не считается
    GP-атрибутом.
  - `DISTRIBUTED BY (колонки)` / `DISTRIBUTED RANDOMLY` / `DISTRIBUTED REPLICATED`;
    параметры `WITH (...)` остаются в parse view и сохраняются из AST как
    `storage_parameters` (`appendonly`, `compresstype`, `compresslevel`, …).
  - Маскирование только распознанных конструкций по лексическим границам:
    паттерны ищутся вне строк, dollar-quoted тел, комментариев и quoted
    identifiers; замена — пробелами с сохранением LF/CR; UTF-8 длина байтов и
    номера строк идентичны оригиналу. Исходные байты и хеши в evidence/manifest
    не меняются (тесты `GreenplumMaskingTests`).
  - Неизвестный текст **не удаляется и не маскируется**: `EXECUTE ON COORDINATOR`,
    нераспознанный `DISTRIBUTED` и т.п. остаются в parse view и дают
    локализованную coverage note (`Unrecognized Greenplum …`); диагностика
    переживает и отказ AST — добавляется в fallback (`sql_extract.py`).
- `scripts/sql_ast.py`: при `dialect='greenplum'` вход нормализуется адаптером
  перед `pglast.parse_sql`/`parse_plpgsql` (parse_plpgsql получает маскированный
  snippet, source_ref/snippets — оригинал). `execute_on` сохраняется в деталях
  DECLARATION; `distributed`/`storage_parameters` — в деталях CREATE/CTAS.
  `dialect=greenplum` **не подменяется** на `postgres`: имя и версия хранятся как
  заданы входом, `unknown` не становится подтверждённой. Поддерживаемые имена —
  `postgres`, `postgresql`, `greenplum`; остальные — прежняя блокировка.
- `scripts/ddl.py`: `catalog`/`reconstruct`/`parse` используют тот же адаптер;
  контекстный DDL (`q_context_gp.sql`) каталогизируется с `distributed` и
  `storage_parameters` на состояниях таблиц; manifest миграций принимает
  `greenplum` (mysql и прочие по-прежнему `unsupported`). `sql_types.column_catalog`
  повторно парсит исходники через тот же адаптер.
- `scripts/build_bundle.py`, `scripts/validation_gate.py`,
  `scripts/regression_adapter.py`, `scripts/run_regression.py`: сквозная передача
  `dialect` (повторный разбор gate и reference-адаптер включены); `execute_on`
  попадает в facts objects, `distributed`/`storage_parameters` — в facts operation
  structure (добавлены в оба независимых structure-whitelist сверки).
- Контракт схем — расширение v2 с обязательной `gp_extension_version: 1`
  при наличии GP-полей. Старые PostgreSQL-артефакты совместимы; GP-комплекты
  до расширения нужно перестроить из исходного SQL, а не дописать метку:
  `schemas/facts.schema.json` — `objects[].execute_on` (enum MASTER/ANY/ALL SEGMENTS),
  `operations[].structure.distributed` (mode BY/RANDOMLY/REPLICATED + columns),
  `operations[].structure.storage_parameters`; поля таблиц сохраняются также
  в `definitions[]`. Все новые поля проверяются
  downstream: `validation_gate._fact_checks` сверяет `execute_on` с независимым
  объявлением (пропуск, подмена и выдумка — ошибки; тесты ниже), structure
  сверяется как раньше. `references/check-policy.json`: правило `signature`
  явно включает declaration execution attributes (Greenplum EXECUTE ON).
- Ожидания Q-01 синхронизированы с границей Q-03: `cases-q.json` для q05 прямо
  фиксировал `must_not_crash_and_must_block_until_q03`; после Q-03
  `expected/q05/decision.json` — `ready`, `expectation_mode` — `must_pass_analysis`;
  `examples/expected/_authoring.py` обновлён как источник генерации ожиданий.
  A4/A5 (`assertions.json`) не тронуты: «before Q-03 it stays blocked» и запрет
  обещать совместимость при `version=unknown` остаются в силе.

**Изменённые файлы реализации/тестов:**

- новые: `scripts/sql_gp.py`, `tests/test_q03_greenplum.py` (30 тестов)
- изменены: `scripts/sql_ast.py`, `scripts/sql_extract.py`, `scripts/ddl.py`,
  `scripts/sql_types.py`, `scripts/build_bundle.py`, `scripts/validation_gate.py`,
  `scripts/regression_adapter.py`, `scripts/run_regression.py`,
  `schemas/facts.schema.json`, `references/check-policy.json`,
  `examples/cases-q.json`, `examples/expected/q05/decision.json`,
  `examples/expected/_authoring.py`, `tests/test_dialect_matrix.py`,
  `tests/test_q02_fallback_diagnostics.py`, `tests/test_q01_expectations.py`

**Приёмка:**

- [x] Малые положительные GP-примеры проходят анализ и полный gate:
      `test_gp_function_with_master_full_gate_ready` (EXECUTE ON MASTER; gate
      `ready`, `publication_authorized=true`) и
      `test_gp_table_with_distribution_full_gate_ready` (DISTRIBUTED BY + WITH).
      Контрольный `q05_gp_master_nested.sql` (фактический SHA-256 в Q03-REVIEW.json;
      acceptance-large.json фиксирует другой, большой SQL):
      анализ без coverage notes — `execute_on=MASTER`, вложенные производные
      таблицы и физический read `q_src.events` сохранены, EXECUTE-операция из
      атрибута не создаётся; полный reference-gate `ready` и на `version=unknown`,
      и на `6.25.3`.
- [x] Удаление/подмена MASTER и DISTRIBUTED в документации обнаруживаются:
      по facts (`test_removing/replacing_execute_on…`, `test_inventing_execute_on…`,
      `test_removing/replacing_distributed…`) и по отрендеренным claims страницы
      (`test_replacing_master_in_page_claims_is_detected`); каждая мутация даёт
      `blocked`/`revise` и `publication_authorized=false`.
- [x] Неизвестные GP-расширения блокируются с локализованной причиной:
      `EXECUTE ON COORDINATOR` → `Unrecognized Greenplum EXECUTE ON target:
      COORDINATOR`; `DISTRIBUTED` без распознаваемого хвоста →
      `Unrecognized Greenplum DISTRIBUTED clause`; `CREATE EXTERNAL TABLE` →
      локализованная ошибка синтаксиса; оба пути (AST и fallback) сохраняют
      диагностику (`test_unknown_gp_extension_stays_blocked_with_localized_reason`,
      `test_unknown_gp_context_file_yields_localized_ddl_diagnostic`).
- [x] Тесты прежнего намеренного отказа Greenplum обновлены без ослабления правил
      для других диалектов: postgres по-прежнему отвергает `EXECUTE ON MASTER` и
      `DISTRIBUTED BY` (`test_greenplum_specific_syntax_blocks_postgres_analysis`,
      `test_postgres_dialect_still_rejects_gp_syntax`,
      `test_postgres_context_parse_still_rejects_gp_ddl`); mysql/oracle/mssql/sqlite
      остаются `Unsupported dialect` в инвентаре, gate и миграциях
      (`test_unsupported_dialect_in_inventory_blocks_gate`,
      `test_mysql_migration_manifest_still_unsupported`); MERGE-гейт версии не
      менялся. Замены имён: `test_greenplum_dialect_creates_note` →
      `test_greenplum_dialect_supported_subset_has_no_unsupported_note`,
      `test_greenplum_specific_syntax_blocks_analysis` →
      `test_greenplum_specific_syntax_blocks_postgres_analysis` +
      `test_greenplum_adapter_accepts_bounded_subset` +
      `test_greenplum_unknown_extension_stays_blocked`;
      `test_path_a_gp_table_context_stays_blocked` →
      `test_path_a_gp_table_context_parses_with_adapter` +
      `test_path_a_unknown_gp_table_extension_stays_blocked`.
- [x] Тот же адаптер в повторном разборе gate:
      `test_gate_reanalysis_uses_the_same_greenplum_adapter` — пересобранный
      инвентарь восстанавливает атрибут и отличается от подделанного сохранённого.
- [x] Нормализация не удаляет неизвестный текст и сохраняет байты/строки:
      `test_mask_preserves_utf8_byte_length_and_line_count`,
      `test_mask_never_touches_dollar_bodies_or_strings`,
      `test_original_input_hash_and_source_ref_keep_original_bytes`.

**Проверки:**

| Команда | Каталог | Результат |
|---|---|---|
| `python -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 90 passed (Q-01/Q-02/Q-03) |
| `python -B -m unittest discover -s tests -p "test_q03_greenplum.py"` | wiki-doc/wiki-doc | 30 passed |
| `python -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 41 passed |
| `python -B -m unittest discover -s tests` (полный набор) | wiki-doc/wiki-doc | 800 tests, 1 skipped, 0 failed (382s) |

Таблица выше — результаты исходной реализации до повторного ревью.
После исправлений: все 24 unit-модуля — 835 tests / 1 skipped / 0 failed,
Q-набор — 125 tests; reference/saved исторических 01–12 — по 45/45
(три повтора каждого из 15 предметов). q05 с обоими исходными контекстами —
3/3 изолированных reference-gate. Quick skill validation и diff --check пройдены.
Настоящий LLM-прогон — **нет** (Q-08).

**Ограничения:**

- Поддержан только перечисленный GP-поднабор. Семантика GP-версий, системные
  каталоги и поведение функций не проверялись; совместимость с версией сервера
  не подтверждается — q05-A5/D12 остаётся в силе (`version=unknown` — честный
  unknown, совместимость не обещается).
- Вывод типов (`sql_types`/`identity`) оставлен PostgreSQL-общим; отдельные
  GP-типы/алиасы не добавлялись и не выдумываются (вывод типов отделён от
  поддержки разбора, как и требовал план).
- Подмена MASTER/DISTRIBUTED в произвольной поясняющей прозе вне claims-контракта
  — предмет Q-07; здесь механически закрыты facts и rendered claims (`claims-v1`).
- Матрица, SQL-support, facts, SKILL, writer/validator и regression instructions
  синхронизированы с Q-03. Исторический прогон Q-02 выше сохраняет прежние имена.
- GP-DDL внутри dollar-quoted тела, BY с opclass, partition после распределения
  и серверная совместимость не заявлены. Большой SQL с закреплённым хешем
  даёт inventory и 179 coverage notes; это диагностика, не приёмка Q-04.
- `run_regression.check_run` сравнивает gate с ожидаемым решением через
  `publication_authorized` и потому не принимает ожидаемый `blocked`; для Q-набора
  после Q-03 (все решения `ready`) это не мешает, но проявится на отрицательных
  сценариях Q-08 — отнести к Q-08.

**Блокировки:** не зафиксированы.

**Следующее действие:** Q-04 — довести анализ тела, зависимостей и DDL до
контрольного объекта `ckr_uup_db_onboarding.sql`: связь параметр → присваивание →
условие IF → операции ветки (p_retro/v_retro), все PL/pgSQL-блоки, области CTE,
точные детали выражений, восстановление DDL по доказанному порядку миграций;
приёмка — 77 INSERT / 77 DELETE / 1 UPDATE, 36 KPI и 68 пар KPI/структура,
30 уникальных физических источников.
