# Проверка Q-09 «Публичный контракт и статус» — 2026-09-24

Проверены [план Q-этапа](IMPROVEMENT-PLAN-2026-09-23.md),
[журнал Q-этапа](IMPROVEMENT-STATUS-2026-09-23.md),
[актуальный статус](IMPLEMENTATION-STATUS.md), `CHANGELOG.md`, матрица диалектов,
инструкции запуска и фактические артефакты тестов/мутаций. Q-09 не закрыт:
критерий «единый отчёт с измерениями LLM-прогонов» зависит от Q-08.

Повторная проверка всего плана и дополнительные исправления этой же даты —
[REVIEW-PLAN-2026-09-24.md](REVIEW-PLAN-2026-09-24.md). Она уточняет статусы,
шаблоны prepare/verify/blocked и regression runner; измерения ниже относятся
к предыдущей проверке Q-09.

## Замечания и исправления

1. **CHANGELOG завышал покрытие Q-07.** Записи «D01-D12 mutations across all Q
   cases» и «q06 page_assertions.json: added D12 mutations» противоречили факту:
   `tests/test_q07_mutations.py::test_implemented_review_labels_and_explicit_d12_gap`
   требует ровно D01–D11 и фиксирует D12 как незакрытый, а `page_assertions.json`
   q06 содержит D11-мутации двух целей INSERT. Mutation-report: 40 запланировано,
   36 выполнено, 4 q09 `baseline_invalid`, 0 false-ready, `valid: false`.
   Исправлено: записи приведены к D01–D11 с явным перечнем открытых D12/q09/графа/прозы.
2. **Статусы противоречили журналу.** В плане значилось «Q-05…Q-09 — planned»,
   в `IMPLEMENTATION-STATUS.md` — «Q-08/Q-09 — planned», хотя Q-05/Q-06/Q-07/Q-09
   велись и отражены `in_progress`. Устаревшее «72 нераскрытых wildcard» заменено
   фактическими 3 (физические INI-таблицы без DDL) + 1 EXCEPTION.
3. **Оставшиеся пункты Q-09 по документации не были выполнены** (сессия
   остановилась на CHANGELOG):
   - GP-поднабор уже описывался в [dialect-support.md](wiki-doc/docs/dialect-support.md)
     и [sql-support.md](wiki-doc/references/sql-support.md), но §2.2 не давал
     ссылку на отрицательный тест каждой новой конструкции;
   - не было шаблонов ответа для статусов Q-05 и правила legacy/provenance;
   - не было указателя CI-проверки происхождения в regression.md.
   Исправлено: §2.2 получил пары «положительный/отрицательный тест»;
   добавлены §9–10 в `docs/answer-templates.md`; абзац в `references/regression.md`.
4. **Дата-строка матрицы** сообщала «DDL-приёмка Q-04 не завершена». DDL-приёмка
   пройдена (`DdlAcceptanceTests` 2/2), но Q-04 не завершён из-за wildcard/EXCEPTION;
   формулировка исправлена.

## Проверка фактов после правок

- Полный набор: **1076 tests / 1075 passed / 1 skipped / 0 failed**, 255.609 с,
  четыре параллельных процесса, `WIKI_DOC_ACCEPTANCE_PROJECT` задан (все три
  внешних acceptance-теста выполнены, пропуск — Windows symlink). После
  финальных правок `docs/dialect-support.md` адресно перепроверены ссылки и
  фингерпринты документов: `test_p1_acceptance` 12/12,
  `test_p0_gate_regressions` 36/36 (1 skip).
- Q-набор (18 модулей `test_q0*`) — **366**; Q-05 — **98**; Q-07 — **24**;
  `test_review_fixes` — 41; `test_dialect_matrix` — 41; `test_p0_gate_regressions` —
  36 (1 skip); `test_inventory_plan` — 22.
- Ссылки Markdown всего пакета: `test_p1_acceptance` 12/12.
- Quick skill validation: `Skill is valid!`; `git diff --check` — без ошибок.
- Reference Q-набора и мутации **повторно не запускались**: использован
  сохранённый отчёт `.runtime/q07-d12-audit/mutations-q/mutation-report.json`
  (36/36 выполненных обнаружены, 0 false-ready, 4 непроверенных q09,
  `valid: false`). Это измерение предыдущего аудита, не новый прогон.
- Настоящий LLM-прогон не выполнялся (Q-08).

## Открытая приёмка

- Q-09: единый отчёт с измерениями LLM-прогонов (Q-08).
- Q-07: D12, содержательные мутации прозы/графа, q09.
- Q-04: 3 wildcard физических INI-таблиц без DDL и 1 переход в EXCEPTION.
- Q-01: воспроизводимый комплект проверок старой документации на внешнем проекте.
- Q-05: автоматическая передача контекста writer/validator и возобновление
  всей цепочки.

## Доказательства

`.runtime/q09-docs-verify/unit-summary.json` и логи модулей (каталог игнорируется
Git); `.runtime/q07-d12-audit/mutations-q/mutation-report.json`;
`examples/fixtures/acceptance-large.json`. Команды выполнялись из
`wiki-doc/wiki-doc`.
