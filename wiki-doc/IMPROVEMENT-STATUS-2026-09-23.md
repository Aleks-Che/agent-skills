# Статус доработок wiki-doc — 2026-09-23

План: [IMPROVEMENT-PLAN-2026-09-23.md](IMPROVEMENT-PLAN-2026-09-23.md).
Этот файл — журнал выполнения задач Q-01…Q-09. История предыдущего этапа находится
в [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md).

**Текущий аудит content claims, 2026-09-24:** исправлены ложные допуски/отказы
проверки версии, даты и примеров вызова. Введён разбор Markdown и SQL AST,
проверяются схема, сигнатура, аргументы и ограниченные типы констант.
FILTER/OVER и неподтверждённые части SQL-примера не получают допуск.
Прежние отметки о полном D12, причине окна и равнозначных объяснениях сняты:
причину нельзя подтвердить совпадением даты, а исправление смысла не равно парафразу.
Финальный прогон: **1135 tests / 1134 passed / 1 skipped / 0 failed**,
41 модуль, 307.234 с, четыре процесса; Q-набор — **414**, новые тесты — **25**.
Все три внешних acceptance-теста выполнены; пропуск — Windows symlink.
Reference и saved, по три повтора: исторические **45/45**, Q **33/33**.
Мутации annotations: **44/44** и **40/40**, **0 untested / 0 false-ready**,
`valid: true`; содержательная причина окна этим не проверяется.
Отчёт: [REVIEW-D12-CONTENT.md](REVIEW-D12-CONTENT.md), доказательства:
[D12-CONTENT-REVIEW.json](D12-CONTENT-REVIEW.json), `.runtime/d12-prose-audit/`.
Q-07/Q-09 — `in_progress`; Q-08 — `planned`, настоящий LLM-цикл не выполнялся.

**Предыдущая сессия D12 content claims, 2026-09-24 (история):** добавлены
три regex-проверки и 6 тестов `D12ProseMutationTests`. Последующий аудит выявил
ложный ready для неверного знака даты, схемы/аргументов вызова и совместимости,
а также ложные отказы на отрицании совместимости и содержимом строкового литерала.
Сопоставление даты не проверяло причину окна. Заявление о закрытии D12 отозвано;
проверки исправлены в текущем аудите, описанном выше.
Интеграция: `validation_gate.py`, `build_bundle.py`, `run_regression.py`,
`lint.py`. Ограничение: произвольная поясняющая проза, бизнес-обоснования и
языковые парафразы вне паттернов **не проверяются** (согласно плану —
«не обещать полное понимание regex-проверкой»).
Полный прогон той сессии (до исправлений текущего аудита):
**1110 tests / 1109 passed / 1 skipped / 0 failed** (253.344 с, четыре параллельных
процесса; сводка — `.runtime/d12-content-verify/unit-summary.json`; все три
внешних acceptance-теста выполнены). Таблица ниже — адресные прогоны ключевых
модулей. Reference Q **11/11**; мутации annotations **40/40**, `valid: true`.

**Проверки D12 content claims (2026-09-24):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -X utf8 -B -m unittest tests.test_q07_mutations.D12ProseMutationTests` | wiki-doc/wiki-doc | 6 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q07_mutations.py"` | wiki-doc/wiki-doc | 33 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q09_model_review.py"` | wiki-doc/wiki-doc | 12 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p0_gate_regressions.py"` | wiki-doc/wiki-doc | 36 tests, 1 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_regression_decisions.py"` | wiki-doc/wiki-doc | 11 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_coverage_gate.py"` | wiki-doc/wiki-doc | 87 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p1_acceptance.py"` | wiki-doc/wiki-doc | 12 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p2_*.py"` | wiki-doc/wiki-doc | 88 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q01_expectations.py"` | wiki-doc/wiki-doc | 30 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q04_ddl_star.py"` | wiki-doc/wiki-doc | 32 tests, 2 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_artifact_regressions.py"` | wiki-doc/wiki-doc | 49 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_evidence_bundle.py"` | wiki-doc/wiki-doc | 55 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_publish.py"` | wiki-doc/wiki-doc | 11 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_index_query.py"` | wiki-doc/wiki-doc | 29 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_artifacts.py"` | wiki-doc/wiki-doc | 30 tests, 0 failed |
| `scripts/run_regression.py --mode reference` (Q-набор) | wiki-doc/wiki-doc | 11/11 ready, 0 errors |
| `scripts/regression_mutations.py` (Q-набор) | wiki-doc/wiki-doc | 40/40 detected, 0 false_ready, valid: true |

Python 3.12.7. SQL в БД не исполнялся. LLM-цикл (Q-08) не запускался.
Изменённые файлы: `scripts/content_claims.py` (новый),
`scripts/validation_gate.py`, `scripts/build_bundle.py`,
`scripts/run_regression.py`, `scripts/lint.py`,
`tests/test_q07_mutations.py` (D12ProseMutationTests, 6 тестов).

**Предыдущий аудит q09/D12, 2026-09-24:** закрыт обход проверки типов через null,
сохранены проверяемые SQL-варианты присваиваний и независимые unknown-обязательства.
Полный прогон: **1104 tests / 1103 passed / 1 skipped / 0 failed**, 40 модулей,
292.812 с, четыре процесса; Q-набор — **383**, новые тесты модели — **12**.
Все три внешних acceptance-теста выполнены; единственный пропуск — Windows symlink.
Reference и saved, по три повтора: исторические **45/45**, Q **33/33** (11/11 каждый раз).
Мутации annotations: **44/44** исторических и **40/40** Q обнаружены,
**0 untested / 0 false-ready**, `valid: true`. D12 о смысле объяснений и примеров
вызова этим не проверен; статус Q-07/Q-09 остаётся `in_progress`, Q-08 — `planned`.
Отчёт: [REVIEW-Q09-MODEL.md](REVIEW-Q09-MODEL.md); доказательства:
[Q09-MODEL-REVIEW.json](Q09-MODEL-REVIEW.json), `.runtime/q09-model-audit/`.

**Предыдущий аудит плана, 2026-09-24:** Q-02/Q-03 — `done`,
Q-01/Q-04/Q-05/Q-06/Q-07/Q-09 — `in_progress`, Q-08 — `planned`.
Исправлены обработка ожидаемых отказов в regression runner, фиктивные числа
ремонта и успешность пустого цикла, статус исходного плана и шаблоны Q-05.
Новый полный прогон: **1087 tests / 1086 passed / 1 skipped / 0 failed**,
39 модулей, 342.219 с; Q-набор — **366**, новые regression-тесты — **11**.
Все три внешних acceptance-теста выполнены. Reference и saved, по три повтора:
исторические **45/45**, Q **30/33** (только q09). Мутации: **44/44** исторических,
**36/36** выполненных Q обнаружены, **4** q09 не проверены, **0 false-ready**;
общая Q-приёмка `valid: false`. LLM-прогон не выполнялся.
Отчёт: [REVIEW-PLAN-2026-09-24.md](REVIEW-PLAN-2026-09-24.md);
доказательства: [PLAN-REVIEW-2026-09-24.json](PLAN-REVIEW-2026-09-24.json),
`.runtime/plan-audit-2026-09-24/` относительно `wiki-doc/`.

**Предыдущая сессия 2026-09-24 (q09 model fix + заявленный D12):**
`build_bundle.py` начал сводить разные выражения одной колонки к null;
`check_types` при этом перестал проверять тип любой колонки без expression.
Повторный аудит обнаружил ложный ready на выдуманном типе text у q08/q09
и потерю доступных проекций q09. Это исправлено сохранением полных SQL-вариантов
в query операций, независимыми unknown-обязательствами и обязательной сверкой
одиночных mappings. Простое обнуление больше не разрешает ready.
Три `D12ContentMutationTests` проверяли только добавление поля claims, подмену
SQL-условия и имени объекта. Они переименованы в `ClaimTableMutationTests`;
содержательная приёмка D12 и новых эквивалентных объяснений **не подтверждена**.
Числа прежнего прогона (11/11, 40/40) ниже сохранены как история измерений,
а не доказательство отсутствия выявленного обхода.

**Проверки сессии q09 fix + D12 (2026-09-24):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -X utf8 -B -m unittest discover -s tests -p "test_q07_mutations.py"` | wiki-doc/wiki-doc | 27 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q04_ddl_star.py"` | wiki-doc/wiki-doc | 32 tests, 2 skipped (acceptance), 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q01_expectations.py"` | wiki-doc/wiki-doc | 30 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p0_gate_regressions.py"` | wiki-doc/wiki-doc | 36 tests, 1 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_regression_decisions.py"` | wiki-doc/wiki-doc | 11 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_coverage_gate.py"` | wiki-doc/wiki-doc | 87 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p2_*.py"` | wiki-doc/wiki-doc | 88 tests, 0 failed |
| `scripts/run_regression.py --mode reference` (Q-набор) | wiki-doc/wiki-doc | 11/11 ready, 0 errors |
| `scripts/regression_mutations.py` (Q-набор) | wiki-doc/wiki-doc | 40/40 detected, 0 false_ready, valid: true |

Python 3.12.7. SQL в БД не исполнялся. LLM-цикл (Q-08) не запускался.

**Предыдущий итог 2026-09-24 (Q-09 частично):** Q-02/Q-03 — `done`,
Q-01/Q-04/Q-05/Q-06/Q-07/Q-09 — `in_progress`, Q-08 — `planned`.
CHANGELOG.md обновлён записями Q-05/Q-06/Q-07; формулировка Q-07 исправлена
на фактическое покрытие D01–D11 (D12 и q09 остаются открытыми).
Полный прогон после правок Q-09, 2026-09-24: **1076 tests / 1075 passed /
1 skipped / 0 failed** (255.609 с, четыре параллельных процесса; Python 3.12.7,
pglast 7.14, `WIKI_DOC_ACCEPTANCE_PROJECT` задан; Q-набор — **366**). После
финальных правок матрицы адресно перепроверены ссылки и фингерпринты документов:
`test_p1_acceptance` 12/12, `test_p0_gate_regressions` 36/36 (1 skip).
Reference Q-набора — **10/11**; мутации — **36/36** выполненных обнаружены,
**4** q09 `baseline_invalid`, **0** false-ready, общий `valid: false`.
Аудит и список открытых критериев: [REVIEW-Q09.md](REVIEW-Q09.md).

Предыдущая сессия 2026-09-24: начало Q-09 и записи CHANGELOG по Q-05/Q-06/Q-07
(до сверки с [REVIEW-Q07-D12.md](REVIEW-Q07-D12.md)).

Предыдущий аудит 2026-09-24: повторный аудит D12 восстановил точное сравнение
с `_authoring.py` и полный набор исполненных мутаций. Подробности:
[REVIEW-Q07-D12.md](REVIEW-Q07-D12.md).

Предыдущий аудит 2026-09-24: аудит Q-07 добавил реальный claims/SQL-gate,
положительные контроли и проверку применимости мутаций. Подробности:
[REVIEW-Q07-MUTATIONS.md](REVIEW-Q07-MUTATIONS.md).

Предыдущий аудит 2026-09-24: аудит Q-06 уточнил инструкции «Большие объекты»
и сборку срезов/coverage. Подробности: [REVIEW-Q06-WRITER.md](REVIEW-Q06-WRITER.md).

Предыдущий аудит 2026-09-24: повторный аудит Q-05 resume исправил проверки
артефактов и фактическое продолжение manifest/gate/publication. Подробности:
[REVIEW-Q05-RESUME.md](REVIEW-Q05-RESUME.md), [Q05-RESUME-REVIEW.json](Q05-RESUME-REVIEW.json).

Предыдущий аудит 2026-09-24: повторный аудит finalize/provenance Q-05 исправил
принятие подменённых inventory/plan и чужого run_id. Подробности:
[REVIEW-Q05-FINALIZE.md](REVIEW-Q05-FINALIZE.md), [Q05-FINALIZE-REVIEW.json](Q05-FINALIZE-REVIEW.json).

Предыдущий аудит 2026-09-24: повторная проверка Q-05 исправила принятие
подменённых inventory/plan и чужого run_id. Подробности:
[REVIEW-Q05-PREPARE.md](REVIEW-Q05-PREPARE.md), [Q05-PREPARE-REVIEW.json](Q05-PREPARE-REVIEW.json).

Предыдущий аудит 2026-09-24: повторная проверка reference-ожиданий Q-01
подтвердила 10/11; восстановлены `date_boundary` у q04 и `unknown` у q11.
Подробности: [REVIEW-Q01-REFERENCE.md](REVIEW-Q01-REFERENCE.md),
[Q01-REFERENCE-REVIEW.json](Q01-REFERENCE-REVIEW.json).

Предыдущий аудит 2026-09-24: аудит set-операций реализовал раскрытие SELECT *
через CTE, производные таблицы и UNION ALL. Wildcard-gap контрольного SQL
снижен с 71 до **3**. Подробности: [REVIEW-Q04-SETOPS.md](REVIEW-Q04-SETOPS.md),
[Q04-SETOPS-REVIEW.json](Q04-SETOPS-REVIEW.json).

Предыдущий аудит 2026-09-24: повторная проверка CTE/derived wildcard выявила
обрезание колонок частичным списком CTE-имён, недоказанное раскрытие неизвестной
ширины, смешение CTE/FROM/физических имён, незавершённую итерацию и падение build
без source_ref. Исправлено; добавлены 15 независимых регрессионных тестов.
Подробности: [REVIEW-Q04-WILDCARD.md](REVIEW-Q04-WILDCARD.md),
[Q04-WILDCARD-REVIEW.json](Q04-WILDCARD-REVIEW.json).

Исторический результат повторного ревью Q-03 (не новый прогон Q-04):
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
| Q-04 | Анализ тела функции, зависимостей и DDL | in_progress |
| Q-05 | Проверяемый запуск и происхождение результата | in_progress |
| Q-06 | Генерация больших страниц без потери логики | in_progress |
| Q-07 | Независимая проверка содержания и мутации | in_progress |
| Q-08 | Настоящий LLM-цикл и приёмка на реальном SQL | planned |
| Q-09 | Документация поддержки и итоговый статус | in_progress |

## Подробности выполнения

### Q-01. Зафиксировать независимые ожидания

**Статус:** in_progress.
**Обновлено:** 2026-09-24.
**Исполнитель:** opencode (`xiaomi-token-plan-sgp/mimo-v2.6-pro`).

**Повторная проверка и исправления:** Codex, 2026-09-23 и 2026-09-24.

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
- На исходном этапе Q-01 десять случаев ожидали `ready`, q05 — `blocked`. После
  Q-03 q05 также ожидает `ready`. Текущий reference-gate: 10/11, три повтора;
  q09 сохранял ожидаемый ready и отвергался из-за модели колонок. Текущий аудит
  восстановил ready с сохранением всех SQL-вариантов; подробности в отчёте выше.
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

**Следующее действие (обновлено после q09 fix):** закрыть приёмку
Q-01 — воспроизводимый комплект проверок ошибок старой документации; затем Q-05.
Reference-ожидания Q-набора: **11/11** (q09 исправлен с сохранением SQL-вариантов).

### Q-04. Довести анализ тела, зависимостей и DDL до контрольного объекта

**Статус:** in_progress (повторное закрытие не подтверждено аудитом).
**Обновлено:** 2026-09-24.
**Исполнители:** opencode — исходная реализация и финальная сессия; Codex — повторная проверка и исправления.

**Выполнено и повторно проверено:**

- IF/ELSIF/ELSE: guards сохраняют вложенность и порядок; следующие ветки
  требуют `(предыдущее условие) IS NOT TRUE`, включая NULL. DDL, CTAS, CALL,
  EXECUTE, CTE и RETURN также сохраняют guards/branch.
- GET [STACKED] DIAGNOSTICS: отдельный ASSIGN на каждую цель, вид значения,
  stacked и source_ref; 159 присваиваний на контрольном SQL, включая четыре
  в EXCEPTION. Неразрешимая цель остаётся gap. Ранее диагностика исчезала.
- RAISE: исправлен INFO; сохранены все шесть SQL-уровней, message, arguments,
  условие/SQLSTATE, USING, rethrow, reads/calls. Выражения имеют отдельные
  formula-обязательства, вложенные SELECT обходятся.
- EXCEPTION: сохранены действия обработчиков и повторные audit-вызовы;
  неизвестный момент перехода в обработчик остаётся блокирующим ограничением.
- ASSIGN: цель по varno/datums, лексическое выделение :=/= с учётом кавычек
  и вложенности. Имя переменной `"a=b"` больше не ломает разбор.
- CTE-области и derived_aliases различаются; сохраняются ORDER BY/LIMIT/OFFSET,
  NULL-полярность, CASE и IN. Структурные поля повторно сверяются полным gate.
- `sql_types.column_catalog` теперь сопоставляет позиционный INSERT с явным
  списком SELECT по колонкам из доказанного DDL/manifest. Проверка q08
  подтверждает порядок `(id, amount, legacy, total)` после DROP/ADD.
- Инструкции SKILL, facts, sql-support и матрица поддержки обновлены.
- `test_q04_review.py`: 19 новых регрессий, включая три мутации полного gate
  с пересозданными страницей, validation и хешами. Отказ вызван сверкой SQL,
  а не старым хешем. Первые 16 тестов до исправлений воспроизводили дефекты.

**Реализация поздней сессии 2026-09-24 (уточнена повторным аудитом):**

- **Раскрытие SELECT *:** `sql_ast.from_relations`/`star_targets` фиксируют
  источники wildcard на уровне FROM; `sql_types.expand_wildcard_outputs`
  раскрывает `*`/`alias.*` по установленным колонкам (context/manifest/CREATE)
  в порядке FROM и пересчитывает wildcard-gap (успешная развёртка убирает
  заметку, нераскрытая сохраняет блокировку). `expand_star_outputs` сопоставляет
  `INSERT … SELECT *` с колонками цели. Внешняя развёртка без установленной
  структуры источника честно остаётся gap — общая развёртка не заявлена.
- **Каталог-зависимый DROP/ADD:** `ddl.do_drop_columns` принимает только
  доказуемый шаблон DO по pg_attribute (литеральные schema/table/columns,
  `attname =`/`IN`, стражи `attnum > 0` и `NOT attisdropped`, join по oid,
  единственная динамическая команда ровно `ALTER TABLE … DROP COLUMN …`);
  условный DROP выполняется против baseline из manifest. Любая другая
  DO-программа — `unsupported` с локальной причиной (не «DoStmt» вообще).
  Приняты сопутствующие операторы миграций: `DROP VIEW IF EXISTS`, `TRUNCATE`,
  `UPDATE` (data-only). В статическом контексте принят шаблон
  `DROP TABLE IF EXISTS … → CREATE` той же таблицы позже в одном файле;
  DROP без последующего CREATE остаётся unordered-блокировкой.
- **Приёмка DDL реального проекта:** `tests/test_q04_ddl_star.py::DdlAcceptanceTests`
  строит manifest по закреплённому списку и SHA-256 из `acceptance-large.json`
  (4 CREATE-файла, 10 таблиц; 6 ALTER-файлов). Прежняя сортировка glob удалена;
  это приёмочная последовательность, не доказательство production-порядка.
  Тест сверяет восстановленное состояние: meet_tasks
  ровно 31 колонка с типами ревью, main 25 с `product int2` в конце после
  DO-drop/ADD, new_clients 29 с `active_products_90 numeric(8, 2)` после
  ALTER TYPE; `DISTRIBUTED`/`storage_parameters` (включая `orientation = row`)
  на всех трёх. Второй тест прогоняет control SQL SHA-256 `168dc680…cb4941c3`
  через `column_catalog` c manifest и проверяет, что все mapping-колонки
  целевых таблиц существуют в восстановленном DDL. Оба теста — 2/2 при
  `WIKI_DOC_ACCEPTANCE_PROJECT=C:/work/git/my-repos/test-sql-wiki`.
- **Семантика last_day/add_months:** именованные контракты
  (`EXTERNAL_FUNCTION_CONTRACTS`, семантика Oracle SQL Reference) вместо
  «Unresolved unqualified call»: вызов идентифицируется при правильном числе
  аргументов; именованный Oracle-контракт обеих функций возвращает `date`.
  Прежнее копирование типа аргумента add_months исправлено по первоисточнику;
  доступность/поведение на целевом сервере фиксируется в facts `unknowns`
  (правило `unknown` неблокирующее) и не подменяет проверку страницы.
  Семантика не выдумывается: контракт цитируется, серверная совместимость
  не подтверждается. Аналогично `make_interval` отнесён к документированным
  PostgreSQL builtin (замечание q11).
- **Побочно закрыт пробел Q-03:** `WITH (orientation = ROW)` не парсился
  PostgreSQL-грамматикой (ROW — reserved keyword). Адаптер маскирует GP-only
  значение в parse view с сохранением байтов/строк и восстанавливает его как
  `storage_parameters['orientation']='row'` из лексического construct;
  DISTRIBUTED-доказательство парсит нормализованный фрагмент. Положительный
  тест `GreenplumStorageValueTests`.

**Реализация CTE/derived wildcard expansion после повторного аудита (2026-09-24):**

- **`sql_ast.from_relations`:** убрана ранняя ошибка `withClause` → `None`;
  CTE-источники возвращаются как `(alias, cte_name)` для разрешения через `env`;
  `RangeSubselect` возвращает `(alias, alias_name)` для derived-таблиц.
  USING/NATURAL JOIN и переименованные алиасы по-прежнему остаются unresolved.
- **`sql_ast.analyze_query`:** scoped-ссылки derived отделены от `env` CTE;
  фактический результат подзапроса связан через `result_for`. Вложенные
  wildcard-проекции распространяются после раскрытия внутренних SELECT.
  Квалифицированное физическое имя не разрешается как имя CTE с точкой.
  **Set-операции (UNION/INTERSECT/EXCEPT):** рекурсивный анализ операндов с
  учётом left-ассоциативности PostgreSQL — `a UNION b UNION c` строится как
  `(a UNION b) UNION c`, поэтому средний операнд живёт на `larg.rarg` и
  обходится рекурсивно вместе с собственным WITH и клаузами каждого узла.
  `set_operation` хранит оператор/ALL и якоря обоих операндов; `result_for`
  устанавливается на родительском результате, не на листе. Порядок листьев
  соответствует исходнику. Имена берутся слева после проверки одинаковой
  установленной ширины; выражение/общий тип прямого set-результата — unknown.
- **`sql_types.expand_wildcard_outputs`:** итерация до остановки прогресса,
  без лимита 10 и без сравнения только количества unresolved-операций.
  Колонки CTE/derived регистрируются вместе с `source_ref`; частичные
  `aliascolnames` переименовывают префикс, сохраняя хвост. Список имён не
  устанавливает неизвестную ширину; избыток имён сохраняет wildcard-gap.
- **`sql_types.expand_star_outputs`:** тот же порядок колонок при позиционном
  INSERT через вложенные CTE/derived. Локальный источник, включая unresolved,
  скрывает одноимённую физическую таблицу; квалифицированная таблица доступна.
- **Сценарии предыдущих сессий** (включая два UNION-теста):
  `test_star_through_cte_expands_from_cte_columns`,
  `test_star_through_derived_table_expands`,
  `test_cte_aliascolnames_override_select_names`,
  `test_nested_cte_wildcard_resolves_through_chain`,
  `test_union_all_cte_expands_from_left_operand`,
  `test_three_way_union_inventories_all_operands` — ловит пропуск
  среднего операнда в left-ассоциативной цепочке `a UNION b UNION c`,
  обновлён `test_cte_shadow_does_not_use_physical_table_columns`.
  Wildcard-gap контрольного SQL: 72 → 71 → **3** после `column_catalog`.
- **Повторный аудит:** 15 новых тестов `test_q04_wildcard_review.py`;
  протокол и результаты — [REVIEW-Q04-WILDCARD.md](REVIEW-Q04-WILDCARD.md).
- **Аудит set-операций:** ещё 15 тестов `test_q04_setops_review.py`; полные
  gate-контроли и мутации с актуальными хешами. Первые 11 новых тестов дали
  19 отказов на исходном коде; после исправлений все 15 проходят.

**Приёмка тела на SQL SHA-256 `168dc680…cb4941c3`:**

- [x] 77 INSERT / 77 DELETE / 1 UPDATE; main — 68 расчётных + 1 ретро.
- [x] 36 KPI / 68 пар, 30 физических FROM/JOIN-источников.
- [x] Не только числа: хеши нормализованных наборов всех DML `(kind,target,line)`,
      физических источников и KPI-пар совпадают с независимым ревью. Его SHA-256
      и метод нормализации закреплены в `examples/fixtures/acceptance-large.json`.
- [x] Три конкретные ретро-пары main/new_clients/meet_tasks, условие v_retro,
      граница 01.07.2025 и отсутствие v_date_start/v_date_end в DELETE и SELECT
      источника INSERT. Проверена область объявления операций.
- [x] 164 add_log_add; заключительные init_type_oper/start_oper/add_log_add/
      add_log/end_oper и четыре STACKED-присваивания обработчика не потеряны.
- [x] Все **73** coverage_notes сырого inventory до enrichment порождают blocking analysis_gap:
      wildcard CTE/derived — 72, момент перехода в EXCEPTION — 1.
      Контракты внешних функций сняли девять call-gaps; оставшийся wildcard-анализ
      не был учтён в прежнем заявлении об одном gap.
- [x] Простой SELECT * по установленному DDL реализован; нераскрытые wildcards
      сохраняют блокировку (`WildcardExpansionTests`).
- [x] **Раскрытие SELECT * через CTE, производные таблицы и UNION ALL (2026-09-24):**
      `from_relations` теперь возвращает CTE/derived источники (не `None`);
      `analyze_query` связывает scoped derived-источники с `result_for` и
      обрабатывает set-операции (UNION/INTERSECT/EXCEPT), рекурсивно анализируя
      операнды и беря колонки из левого операнда; `expand_wildcard_outputs`
      строит маппинг колонок CTE/derived и итерирует до фиксированной точки
      для вложенных цепочек; `expand_star_outputs` учитывает CTE-shadowing.
      Wildcard-gap контрольного SQL снижен с 72 до **3** после `column_catalog`
      (остались физические таблицы без установленного DDL). Новые тесты:
      `test_union_all_cte_expands_from_left_operand` и 5 предыдущих.
- [ ] Раскрытие SELECT * через CTE и производные таблицы контрольного объекта:
      3 случая остаются неразобранными (физические таблицы без DDL); Q-04 не завершён.
- [x] Каталог-зависимые динамические DROP/ADD реализованы доказуемым шаблоном;
      `ddl.reconstruct` больше не отклоняет DO-шаблон (`DoMigrationTests`).
- [x] Приёмка DDL и миграционного состояния реального проекта выполнена
      (`DdlAcceptanceTests`, 2/2); приёмочная последовательность и хеши теперь
      закреплены входом. Её нельзя выдавать за проверенный production-порядок.
- [x] Семантика last_day/add_months отражена контрактами с честным unknown;
      пути исключения обходятся с сохранением audit-вызовов.

**Незакрытые критерии и граница готовности:**

- [ ] Q-04: раскрыть оставшиеся 3 wildcard-выхода контрольного объекта
      (физические таблицы `s_gp_p1024_ora_svd_kb_ckr_uup_gp_ini.*` без
      установленного DDL в контексте). UNION ALL CTE-цепочки и вложенные
      derived-таблицы теперь раскрываются.

- [ ] Момент перехода в EXCEPTION остаётся блокирующим analysis_gap —
      намеренное ограничение статического анализа (план: «неизвестный момент
      перехода в обработчик остаётся блокирующим ограничением»). Полный ready
      контрольного объекта требует решения Q-07/Q-09 по статусу этого gap.
- [x] Согласование reference-ожиданий Q-набора: прогон **11/11** (было 5/11,
      затем 10/11). Исправлены deparse-расхождения, неприменимые required_rules
      и ограничение модели одной `column.expression` (q09).
- [x] Модель одной `column.expression` не покрывала несколько выражений одной
      колонки в разных ветках (расхождение q09). Исправлено 2026-09-24:
      `build_bundle.py` сохраняет `expression: null` только для сводки разных
      выражений/типов. `check_types` требует точные варианты в query операций,
      статус unknown и сохраняет обязательную сверку одиночных mappings.
      Null не отключает проверку установленного SQL-выражения или его типа.

**Проверки исходной сессии UNION ALL CTE (2026-09-24, история):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -X utf8 -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 218 tests, 3 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_sql_ast.py"` | wiki-doc/wiki-doc | 20 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest tests.test_q04_ddl_star.DdlAcceptanceTests` c `WIKI_DOC_ACCEPTANCE_PROJECT` | wiki-doc/wiki-doc | 2 tests, 0 failed |
| `python -X utf8 -B -m unittest tests.test_q04_body_analysis.AcceptanceNumbersTests` c `WIKI_DOC_ACCEPTANCE_PROJECT` | wiki-doc/wiki-doc | 1 test, 0 failed |

Новый тест: `test_union_all_cte_expands_from_left_operand`.
Wildcard-gap контрольного SQL: 72 → 71 → **3** после `column_catalog`.
Python 3.12.7. SQL в БД не исполнялся. LLM-цикл и публикация не запускались.

**Проверки исходной сессии CTE/derived wildcard (2026-09-24, история):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -X utf8 -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 201 tests, 3 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_sql_ast.py"` | wiki-doc/wiki-doc | 20 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_inventory_plan.py"` | wiki-doc/wiki-doc | 22 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p0_gate_regressions.py"` | wiki-doc/wiki-doc | 36 tests, 1 skipped, 0 failed |
| `python -X utf8 -B -m unittest tests.test_q04_ddl_star.DdlAcceptanceTests` c `WIKI_DOC_ACCEPTANCE_PROJECT` | wiki-doc/wiki-doc | 2 tests, 0 failed |
| `python -X utf8 -B -m unittest tests.test_q04_body_analysis.AcceptanceNumbersTests` c `WIKI_DOC_ACCEPTANCE_PROJECT` | wiki-doc/wiki-doc | 1 test, 0 failed |

Сценарии CTE/derived: 5 (`test_star_through_cte_expands_from_cte_columns`,
`test_star_through_derived_table_expands`, `test_cte_aliascolnames_override_select_names`,
`test_nested_cte_wildcard_resolves_through_chain`, обновлён
`test_cte_shadow_does_not_use_physical_table_columns`).
Wildcard-gap контрольного SQL: 72 → 71 после `column_catalog`.
Python 3.12.7. SQL в БД не исполнялся. LLM-цикл и публикация не запускались.

### Q-05. Сделать соблюдение этапов проверяемым

**Статус:** in_progress.
**Обновлено:** 2026-09-24.
**Исполнитель:** opencode (`xiaomi-token-plan-sgp/mimo-v2.6-pro`).
**Повторная проверка и исправления:** Codex, 2026-09-24.

**Выполнено:**

- `scripts/run_prepare.py`, CLI `prepare`/`verify-run`: явные корни и subject,
  UUID и канонический page_id; выбор копии должен совпадать с исполняемым пакетом.
- Используются существующие tool_versions, create_manifest и evidence-проверки.
  Run_context содержит preparation_manifest с SQL, DDL, migration manifest,
  ordered_files, активным профилем и хешами inventory/plan. Runtime-инструкции
  из docs входят в проверку. Runtime_hash — сводка tool_versions.
- План строится до writer с DDL enrichment, column_catalog и активным профилем;
  verify независимо восстанавливает inventory/plan и сравнивает UUID/subject/page_id.
  Подмена содержимого, включая повторное выставление хеша, обнаруживается.
- analysis_gap, неверные входы и неполная запись дают limitation с диагностикой.
  Новый каталог создаётся эксклюзивно; успешный run_context пишется последним.
  При повторном prepare уже существующие файлы не меняются.
- Wiki при prepare не меняется, журнал ведётся внутри run_dir. Verify не пишет
  файлы. Prepared/verified явно не разрешают публикацию и не означают завершение.
- 10 исходных тестов используют полные копии скилла и реальный CLI; добавлены
  23 регрессионных теста. До исправления 17 контрпримеров дали 16 failures и
  4 errors с учётом JSON-подслучаев. Подробности и хеши — Q05-PREPARE-REVIEW.json.
- **Подкоманды `finalize` и `provenance` (2026-09-24):**
  - `finalize` использует общий verify_prepared и принимает готовые writer/validator
    артефакты. Полный manifest сохраняет UUID/runtime/входы/план подготовки.
    Пропуски, смена runtime и подмена данных отклоняются до публикации;
    reference build больше не перезаписывает их автоматически.
  - Publication snapshot привязывается к manifest; merge с изменением draft
    требует нового validator-отчёта. Completed возвращается после полного gate,
    publisher и проверки provenance. Исправлена ошибка повторных keyword flags.
  - `provenance --page` проверяет выбранные страницы: metadata, байты, полный
    архивный gate, UUID/источники, текущий SQL/DDL, committed journal и индекс.
    Пустой каталог/legacy/ручная запись не подтверждают генерацию. Обычный lint
    и не выбранные legacy/audit-файлы не изменены.
  - 6 исходных тестов актуализированы; добавлен 31 тест с настоящими временными
    публикациями, CLI, профилем, миграциями и отрицательными сценариями. До
    исправлений 22 контрпримера дали 30 failures с учётом подслучаев.
- **Подкоманда `resume` (2026-09-24):**
  - `verify_prepared`, проверка схем/связей/UUID/evidence сохранённых артефактов,
    coverage и полного manifest. Наличие файла больше не объявляется выполнением этапа.
  - После готового writer/validator отсутствующий manifest собирается с исходными
    входами/UUID/runtime; finalize выполняет недостающие gate/publication.
    Старый decision не заменяет gate; испорченный существующий manifest не пересоздаётся.
  - Существующий корректный publication snapshot привязывается без перезаписи;
    конфликты с правками редактора сохраняются. Publisher выполняет recovery
    транзакции под своей блокировкой; проверены os._exit и повтор committed-запуска.
  - Частичные авторские этапы возвращают needs_action, код 1, следующий артефакт
    и оба флага готовности false; испорченные данные возвращают blocked.
  - 4 исходных теста переведены на реальные prepare/bundle и точные проверки;
    добавлены 24 регрессии. До исправления: 19 тестов, 15 failures и 1 error
    контрпримера с заглушенным finalize. Импорт в test_q05_run_prepare сохранён.

**Приёмка (частично):**

- [x] Единая точка подготовки запуска с явными путями, UUID и runtime-хешем.
- [x] При отказе возвращается limitation с диагностикой, а не готовая страница.
- [x] Проверка runtime, SQL/DDL/миграций, артефактов, UUID и независимого плана.
- [x] Привязка копии скилла на этапах prepare/verify; отказ вместо молчаливого выбора.
- [x] Проверка одного runtime/UUID в подготовке, готовых артефактах, gate и публикации.
- [x] Строгая проверка происхождения выбранных страниц: metadata, архив, committed publisher.
- [x] Финальный статус только из полного gate, publisher и проверки результата.
- [ ] Автоматическое выполнение writer/validator с передачей подготовленного контекста.
      Проверка их готовых артефактов не доказывает запуск LLM или порядок действий.
- [x] Продолжение manifest/gate/publication после готового writer/validator,
      повторные проверки, recovery проверенной publisher-транзакции и защита конфликтов.
- [ ] Возобновление всей последовательности после прерывания, включая writer/validator
      и неполную начальную подготовку. Needs_action сообщает о недостающем этапе,
      но не является его выполнением. Автоматический LLM-адаптер не настроен.

**Повторная приёмка resume:** полный набор 1052/1051/1/0, Q-набор 342,
Q-05 98; все 28 тестов resume проходят. Реальные CLI, отсутствие manifest/decision/
publication, подмена данных, validator-defect, os._exit, recovery и конфликт
редактора проверены. Команды/логи/хеши — Q05-RESUME-REVIEW.json.

**Историческая приёмка finalize/provenance:** полный набор 1024/1023/1/0,
Q-набор 314, Q-05 70; reference 45/45 и 30/33 в трёх повторах. Все 31 новые
регрессии прошли, в том числе успешная публикация с исходным UUID, отказ при
пропуске validation и подмене данных, merge с повторной валидацией, явный профиль,
миграции, реальный CLI и идемпотентность. Команды/логи — Q05-FINALIZE-REVIEW.json.

**Проверки исходной реализации (история; заменены новым аудитом):**

| Команда | Каталог | Результат |
|---|---|---|
| `python -X utf8 -B -m unittest tests.test_q05_finalize` | wiki-doc/wiki-doc | 6 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 283 tests, 3 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 41 tests, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_p0_gate_regressions.py"` | wiki-doc/wiki-doc | 36 tests, 1 skipped, 0 failed |
| `python -X utf8 -B -m unittest discover -s tests -p "test_inventory_plan.py"` | wiki-doc/wiki-doc | 22 tests, 0 failed |

**Следующее действие:** завершить Q-05 — автоматическая передача контекста
writer/validator и возобновление всей цепочки после прерывания. Затем Q-06/Q-07.
Повтор неизменного committed-комплекта уже поддерживает идемпотентность publisher.


### Q-06. Перестроить работу writer с большим объектом

**Статус:** in_progress.
**Обновлено:** 2026-09-24.
**Исполнитель:** opencode (mimo-v2.6-pro).

**Повторная проверка и исправления:** Codex, 2026-09-24.

**Выполнено:**

- В doc-writer.md добавлен раздел «Большие объекты» с инструкциями:
  - Разделение работы по разобранным операциям/ветвям и семействам KPI.
  - Подготовка срезов контекста с точными source_ref и DDL.
  - Получение счётчиков из структурированных данных (inventory, facts).
  - Использование общих подтверждённых формул KPI с перечнем мест применения.
  - Указание точного типа и выражения для каждой целевой колонки.
  - Различение неизвестной расшифровки аббревиатуры и доступной формулы.
  - Построение графа reads - operation - writes, отдельно calls.
- Убраны отсутствующие в плане порог 20 операций и ограничение только функциями.
  Явно сохранены guards/scope, исходные ID и зависимости срезов; счётчики строятся
  по уникальным операциям в общей области, а не суммированием срезов.
- В SKILL.md описана сборка одной страницы из срезов; в references/facts.md —
  состав контекста и сохранение отдельных ветвей. Общая формула не сокращает план.
- В references/coverage.md описаны объединение ссылок по fact_id, уникальность
  якорей и покрытие выражения, применений и исключений отдельными фрагментами.
  Шаблон 04 ссылается на эти правила; DEFAULT не подменяет DDL-доказательство типа.
- Шаблоны 02, 03, 05, 06 проверены: уже содержат требования к сигнатуре, колонкам,
  графу, ссылкам и неизвестности; изменения ради самого списка файлов не нужны.

**Проверка инструкций (не приёмка страницы):**

- [x] Инструкции по разделению работы для больших объектов.
- [x] Указание на получение данных из структурированных источников.
- [x] Требование различать неизвестные расшифровки и доступные формулы.

**Приёмка по плану (не выполнена):**

- [ ] Проверка на контрольной странице (D01-D12 отсутствуют).
- [ ] Все обязательства раскрыты содержательными фрагментами; одно имя KPI
      без формулы не засчитывается как покрытие.
- [ ] Проверка форматирования, ссылок, графа и рендеринга Mermaid.

**Следующее действие:** устранить оставшиеся ограничения анализа Q-04
(3 wildcard + EXCEPTION), затем выполнить генерацию и содержательную приёмку
контрольной страницы с полным gate. Модель нескольких выражений одной колонки
(q09) исправлена 2026-09-24. Результаты тестов и границы проверки:
[REVIEW-Q06-WRITER.md](REVIEW-Q06-WRITER.md).


### Q-07. Проверять смысл независимо от writer

**Статус:** in_progress.
**Обновлено:** 2026-09-24.
**Исполнитель:** opencode (mimo-v2.6-pro).

**Повторная проверка и исправления:** Codex, 2026-09-24.

**Выполнено:**

- Аудит плана 2026-09-24 исправил `run_regression.check_run`: ожидаемые
  `blocked`/`revise` могут пройти проверку oracle без допуска публикации.
  Диагностика gate сверяется с `decision.json.errors`; посторонний отказ и
  `input_error` не засчитываются. Мутации по-прежнему требуют именно `ready`
  с разрешением публикации. Добавлены проверки на реальных отрицательных
  комплектах в `tests/test_regression_decisions.py`.
- Runner отклоняет пустые наборы и ненулевой `--repair-iterations`: реальный
  автоматический ремонт ещё не реализован, переданное число не выдаётся за
  измерение. Эти исправления не являются LLM-приёмкой Q-08.
  Проверка после исправлений: 1087 tests / 1086 passed / 1 skipped; свежие
  reference/saved и мутации — в [аудите плана](REVIEW-PLAN-2026-09-24.md).
- `test_q07_mutations.py`: 27 тестов, включая три `ClaimTableMutationTests`;
  последние не являются приёмкой D12. Строго проверяются все
  q01…q11, набор меток D01…D11, поля/уникальность аннотаций и связь с `_authoring.py`.
  Реальные комплекты проходят build, видимые claims и независимый SQL-gate;
  изменяются только текст либо согласованно facts/текст. Проверен положительный
  вариант с эквивалентным оформлением Markdown.
- Повторный аудит D12 восстановил строгие сравнения после их ослабления.
  Проверяется связь D-метки с независимым утверждением того же случая;
  новые `D11-exclude-first-target` / `D11-exclude-second-target` выбирают
  разные INSERT и обнаруживаются независимо по `writes`. Подмена только
  `source_refs` не считается проверкой видимой SQL-ссылки. Q-JSON пересобраны
  из `_authoring.py`. Три прежние метки D12 не подтверждали семантику D12.
- `regression_mutations.py`: обязательный повторный положительный контроль;
  `baseline_invalid`, `invalid_mutation` и `unattributed_rejection` не засчитываются
  как обнаружение. Сохраняются реальные ошибки и слой проверки, отдельные счётчики
  запущенных/непроверенных мутаций. Неуникальные селекторы, ошибочные пути и замены
  без изменения значения отклоняются; значения не разделяют изменяемую ссылку с аннотацией.
- D05 теперь проверяет атрибут `execute_on`; D09 — удаление metadata-read и
  устранение WHERE через `true`. Уточнены селекторы q06/q08 и старых 03/07/12;
  изменения Q-ожиданий согласованы с `_authoring.py`.
- В doc-validator и references/regression.md описаны положительные контроли,
  причины отказов и границы claims-проверки. Основной план и статус синхронизированы.

**Приёмка (частично):**

- [ ] Вся матрица D01–D12 покрыта применимыми мутациями. Аннотированы D01–D11;
      `D12ProseMutationTests` и `test_q07_content_review.py` проверяют ограниченные
      утверждения о версии, даты и сигнатуры примеров. Это не приёмка причины окна
      D12 и остальных содержательных подпунктов. `ClaimTableMutationTests` проверяет
      только машинные поля claims.
- [x] Каждый Q-случай имеет минимум одну мутацию.
- [x] Мутации имеют обязательные поля и валидные группы.
- [x] Ни одна размеченная критичная мутация не получает ready: **40/40**
      обнаружены (включая 4 q09 после исправления модели column.expression),
      **0** untested, **0 false-ready**, `valid: true`.
- [ ] Полная содержательная приёмка прозы D12 и графа. Новый модуль улучшает
      механическую проверку; причины окна, смысл графа и непокрытые утверждения
      требуют независимого содержательного прохода.
- [ ] Эквивалентное содержательное объяснение принимается. Смена неверного
      утверждения на верное — положительный контроль, а не равнозначная
      переформулировка. Проверены конкретные формы SQL-примеров, кавычки и оговорки;
      общая приёмка эквивалентных объяснений остаётся открытой.

`facts.dialect.version` существует: отсутствие D12 нельзя объяснять тем, что
версия есть только в run_context. D12 также включает выдуманные причины окна и
неверные примеры; мутация входной версии сама по себе не проверяет эти утверждения.

**Следующее действие:** выполнить содержательную приёмку D12, прозы/графа и
равнозначных объяснений. Ограниченная машинная проверка описана в
[content-claims.md](wiki-doc/references/content-claims.md).
Q-08 не закрывается детерминированным reference-прогоном.

**Проверки до повторного аудита модели:** reference Q-набора — **11/11** за один повтор;
мутации — **40/40** обнаружены, **0** untested, **0** `invalid_mutation`,
**0 false-ready**, `valid: true`. Из 40 обнаружений: 20 сверкой видимых claims,
18 независимой сверкой SQL, 2 проверкой схемы. Адресные тесты Q-07 — 27/27.


### Q-09. Обновить публичный контракт и статус

**Статус:** in_progress.
**Обновлено:** 2026-09-24 (повторная сессия).
**Исполнитель:** opencode (mimo-v2.6-pro).

**Выполнено:**

- Повторный аудит q09/D12 (2026-09-24): обнуление колонок больше не отключает
  проверку SQL-типов; разные ветви сохраняются в query операций с независимой
  сверкой и unknown-обязательствами. Прежние заявления о закрытии D12 и проверке
  эквивалентных объяснений отозваны; три теста переименованы по реальному предмету.
- Аудит плана 2026-09-24 синхронизировал также исходный
  `IMPLEMENTATION-PLAN.md` (Q-09 — `in_progress`). Шаблоны ответов разделяют
  реальные поля `prepare`/`verify-run`, содержат `blocked` и учитывают отказ
  после публикации, а также пустые `diagnostic_paths` при раннем отказе.
  Новый контракт regression runner описан в `references/regression.md`.
  Итоговые измерения текущей сессии приведены в начале этого журнала и в
  [аудите плана](REVIEW-PLAN-2026-09-24.md); результаты первоначального Q-09 ниже
  сохранены как история.
- CHANGELOG.md обновлён записями Q-05/Q-06/Q-07. Формулировка Q-07 исправлена
  на фактическую: аннотации D01–D11, D12/проза/граф открыты;
  успех 40/40 относится только к аннотированному набору. q09 использует
  подтверждённую SQL сводку null вместе с точными query-claims ветвей,
  а не пропуск проверки. Q-05 явно отмечает ручной характер
  writer/validator и `needs_action` при неполном продолжении.
- Поддержанный GP-поднабор и границы описаны в
  [dialect-support.md](wiki-doc/docs/dialect-support.md) §2.2–2.3, §3, §5 и
  [sql-support.md](wiki-doc/references/sql-support.md). В §2.2 каждая новая
  конструкция получила ссылку и на положительный, и на отрицательный тест:
  `EXECUTE ON MASTER/ANY/ALL SEGMENTS`, `DISTRIBUTED BY/RANDOMLY/REPLICATED`,
  `WITH`-параметры (включая `orientation = ROW`), GP MERGE и неизвестные
  расширения. Версия GP по умолчанию `unknown`, совместимость не подтверждается.
- Команды подготовки запуска, выбор копии скилла и корректные blocked/legacy
  примеры: [SKILL.md](wiki-doc/SKILL.md) §2,
  [artifacts.md](wiki-doc/references/artifacts.md#подготовка-запуска-q-05),
  новые разделы 9–10 [answer-templates.md](wiki-doc/docs/answer-templates.md)
    (`prepared`/`verified`/`limitation`/`blocked`/`completed`/`needs_action`, legacy metadata
  и provenance), указатель CI-проверки в
  [regression.md](wiki-doc/references/regression.md).
- Старая приёмка P0–P2 сохранена как история в
  [IMPLEMENTATION-STATUS.md](IMPLEMENTATION-STATUS.md) (разделы P2-05, P2-04,
  P2-03, P1, P0 и «Приёмка предыдущего этапа — 2026-09-19»).
- Статусы [Q-плана](IMPROVEMENT-PLAN-2026-09-23.md) и IMPLEMENTATION-STATUS.md
  приведены в соответствие факту: Q-05/Q-06/Q-07/Q-09 — `in_progress`,
  Q-08 — `planned`; 72 нераскрытых wildcard заменены фактическими 3 + EXCEPTION.
- Исторические измерения (2026-09-24, сессия q09 fix + заявленный D12): полный прогон
  **1092 tests / 1091 passed / 1 skipped / 0 failed** (326.328 с, четыре
  параллельных процесса; лог-сводка — `.runtime/q09-fix-verify/unit-summary.json`;
  все три внешних acceptance-теста выполнены); Q-набор — **371**;
  `test_q07_mutations` — 27 (включая `D12ContentMutationTests`); reference Q —
  **11/11** (до выявления обхода через null и потери точных вариантов q09);
  мутации annotations — **40/40** обнаружены, **0** untested, **0**
  false-ready, `valid: true`. Предыдущий прогон (1076 tests, 10/11 reference,
  36/36+4 untested, `valid: false`) и его файлы в `.runtime/q09-docs-verify/`
  сохранены как история. Настоящий LLM-прогон не выполнялся (Q-08).

**Приёмка (частично):**

- [x] CHANGELOG обновлён для Q-05/Q-06/Q-07 и не завышает покрытие D01–D11.
- [x] Описание поддержанного поднабора GP с ограничениями версий/конструкций
      и ссылками на положительные/отрицательные тесты (реализовано в Q-03/Q-04,
      сверено в этой сессии).
- [x] Команды подготовки запуска, способ выбора копии скилла и примеры
      blocked/legacy-результата (SKILL.md, artifacts.md, answer-templates.md §9–10).
- [x] Старая приёмка сохранена как история; LLM-прогон явно отмечен как
      невыполненный и не подменён unit/reference-числами.
- [x] Общая сверка «инструкции/CLI/схемы/тесты без противоречий» — в текущей
      сессии подтверждён полный unit-прогон 1135/1134/1/0, исправлены проверки
      content claims и завышенные заявления о D12. Reference/saved Q — 33/33
      в трёх повторах, мутации annotations — 40/40, `valid: true`.
      Сверка не закрывает смысловую приёмку прозы; итог по Q-08 требует LLM-прогона.
- [ ] Итоговый отчёт с измерениями LLM-прогонов и приёмки большого SQL — требует
      Q-08; reference/unit его не заменяют.

**Следующее действие:** после Q-08 выпустить единый отчёт с измерениями и
закрыть Q-09. Сейчас Q-09 остаётся частично выполненным. Аудит, исправленные
замечания и открытые критерии: [REVIEW-D12-CONTENT.md](REVIEW-D12-CONTENT.md), текущие
доказательства — `.runtime/d12-prose-audit/`; [REVIEW-Q09-MODEL.md](REVIEW-Q09-MODEL.md),
[REVIEW-Q09.md](REVIEW-Q09.md)
и `.runtime/q09-fix-verify/` сохранены как история.

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
- Исторический дефект `run_regression.check_run` (ожидаемый `blocked` считался
  неуспехом из-за `publication_authorized=false`) исправлен при аудите плана
  2026-09-24. Проверки отрицательных решений и положительных контролей мутаций —
  `test_regression_decisions.py`; настоящий LLM-цикл Q-08 остаётся невыполненным.

**Блокировки:** не зафиксированы.

**Следующее действие:** Q-04 — раскрыть оставшиеся 3 wildcard-выхода
(физические таблицы `s_gp_p1024_ora_svd_kb_ckr_uup_gp_ini.*` без DDL в контексте)
и приёмка Q-01 (согласование reference-ожиданий Q-набора).
UNION ALL CTE-цепочки и вложенные derived-таблицы теперь раскрываются.
