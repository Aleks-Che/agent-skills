# Статус доработок wiki-doc — 2026-09-23

План: [IMPROVEMENT-PLAN-2026-09-23.md](IMPROVEMENT-PLAN-2026-09-23.md).
Этот файл — журнал выполнения задач Q-01…Q-09. История предыдущего этапа находится
в [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md).

**Текущий итог:** Q-02 выполнена. Q-01 выполнена в основном объёме (фикстуры,
независимые ожидания, матрица D01–D12, воспроизведение аварии) и остаётся
`in_progress`: ошибки старой документации закреплены ожиданиями, но комплект
проверок старой сохранённой страницы ещё не выполнен. Это незакрытая приёмка
Q-01; новый LLM-результат отдельно относится к Q-08. Q-03…Q-09 не начаты.
Следующее действие — Q-03 (ограниченный адаптер Greenplum).

Повторная проверка Codex выявила и исправила ошибки Q-фикстур, диагностик и
тестов; подробности — [REVIEW-Q01-Q02.md](REVIEW-Q01-Q02.md).
Результат 757 tests / 1 skipped ниже относится к предыдущей сессии.
**Настоящий LLM-прогон не выполнялся.**

**Итог повторной проверки:** полный набор — 764 теста, 1 skipped, 0 failed
(все 22 модуля unittest в отдельных процессах). Q-01 — 30 тестов, Q-02 — 27.
Reference/saved исторических сценариев 01–12 — по 15/15 объектов, один повтор.
Большой SQL с закреплённым SHA-256: исходный extractor — код 2, текущий — код 0
с блокирующими причинами. Команды, версии, хеши и результаты каждого модуля —
[Q01-Q02-REVIEW.json](Q01-Q02-REVIEW.json).

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
| Q-03 | Поддержка необходимого поднабора Greenplum | planned |
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

**Следующее действие:** Q-03 — явный ограниченный адаптер Greenplum поверх
pglast: `EXECUTE ON MASTER` как атрибут объявления, `DISTRIBUTED BY/RANDOMLY` и
параметры хранения в `ddl.py`, без подмены `dialect=greenplum` на `postgres`.

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
`references/sql-support.md`, новый `tests/test_q02_fallback_diagnostics.py` (27 тестов),
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

**Следующее действие:** Q-03 — явный ограниченный адаптер Greenplum.
