# Q-01: воспроизведение дефектов на исходной реализации

Дата: 2026-09-23. Пакет: `wiki-doc/wiki-doc`, версия `1.0.0` (`scripts/_version.py`).
Исполнитель: opencode (модель `xiaomi-token-plan-sgp/mimo-v2.6-pro`).
План: [IMPROVEMENT-PLAN-2026-09-23.md](../../IMPROVEMENT-PLAN-2026-09-23.md).

Повторная проверка Codex: [REVIEW-Q01-Q02.md](../../REVIEW-Q01-Q02.md).
Она уточняет исходный отчёт ниже: исправлены фикстуры q03/q08/q09, добавлено
ожидание совместимости D12, статические проверки gate/изоляции заменены
выполнением. Таблица команд §5 сохраняет результаты предыдущей сессии.

## 1. Авария extractor (проблема 5 плана)

**Контрольный случай:** [examples/fixtures/q05_gp_master_nested.sql](../examples/fixtures/q05_gp_master_nested.sql)
(GP-атрибут `EXECUTE ON MASTER` после тела + вложенная производная таблица).
Дополнительно минимизированный репродуктор — ниже в разделе 1.3.

### 1.1 Команда и результат ДО исправления (Q-02)

```text
команда:    python -B scripts\sql_extract.py <q05_gp_master_nested.sql> --dialect greenplum
каталог:    wiki-doc\wiki-doc
код:        2
stdout:     пусто
stderr:     {"error": "Unbalanced SQL list"}
версия:     1.0.0
sha256 SQL: 49fd3d012e82d20f359ad838825e07fa4045baf27ffd9b77b421203b0212f466
```

Та же авария на `--dialect postgres` (GP-синтаксис отвергается парсером в обоих
случаях). Минимальный репродуктор:

```sql
CREATE OR REPLACE FUNCTION demo.repro_unbalanced()
RETURNS bigint LANGUAGE plpgsql AS $$
DECLARE n bigint;
BEGIN
    SELECT count(*) INTO n
    FROM (SELECT id FROM demo_src.events WHERE message = 'x') AS s;
    RETURN n;
END;
$$ LANGUAGE plpgsql EXECUTE ON MASTER;
```

- sha256 минимизированного входа: `35faa5b9f4331f710ac7126ebc66fa115c2890acc72c9bd73b81fd2af4df8b0e`
  (274 байта, кодировка UTF-8, перевод строки LF).
- Код возврата: `2`. stderr: `{"error": "Unbalanced SQL list"}`.

Цепочка: `sql_ast.analyze` отвергает `EXECUTE ON MASTER` → `extract_inventory`
(`sql_extract.py:570`, до Q-02) вызывает `_extract_inventory_legacy` **без**
перехвата → `extract_operations` (`sql_extract.py:336`, до Q-02) режет хвост
`FROM`/`USING` плоским `re.split` по `WHERE` внутри скобок →
`sql_syntax.split_top_level` (`sql_syntax.py:92/94`) кидает `ValueError` →
`main` (`sql_extract.py:652`, до Q-02) печатает JSON-ошибку и возвращает 2.

### 1.2 Результат ПОСЛЕ исправления (Q-02)

```text
команда:    python -B scripts\sql_extract.py examples\fixtures\q05_gp_master_nested.sql --dialect greenplum --version unknown
каталог:    wiki-doc\wiki-doc
код:        0
stdout:     полный inventory (schema_version 2), 8 items, blocking coverage_notes
```

Сохранённые блокирующие причины (сокращённо; полный JSON воспроизводится командой):

| Диапазон | Причина |
|---|---|
| весь файл | `Unsupported dialect: greenplum` |
| строки 1..30 | `PostgreSQL AST analysis failed: syntax error at or near "EXECUTE", at index 1053` |
| строки 15..20, 17..20, 22..25 | `Unresolved unqualified call: FROM` |
| строки 22..25 | `SELECT INTO / ON CONFLICT / named window requires scoped analysis` |
| строки 12, 16..17, 17..18, 23..24 | `DECLARE …` / `Nested/set query requires scoped analysis beyond P0` |
| строки 11..13 | `Unanalyzed executable fragment: DECLARE …` |

Аварийного `{"error": "Unbalanced SQL list"}` / кода 2 больше нет. Остаточный
`Source list split failed: Unbalanced SQL list` при разборе **вложенного** SELECT
сохраняется как локализованная coverage note с исходным диапазоном — это
документированная диагностика, а не авария (устранение самой причины
depth-blind развёртки сегментов — предмет Q-04).

С контекстным DDL `q_context_gp.sql` (`DISTRIBUTED BY`) дополнительно даётся
`…: DDL parse failed: syntax error at or near "DISTRIBUTED" …` — снова
диагностика, не авария (до Q-02 этот путь тоже завершался кодом 2).

### 1.3 Проверяемые утверждения по аварии

- [x] Причина аварии локализована до строки и функции.
- [x] Код возврата 2 с `Unbalanced SQL list` воспроизведён на версии 1.0.0.
- [x] После Q-02 контрольный файл не заканчивается недиагностируемой аварией.
- [x] До реализации Q-03 объект остаётся неподдержанным с явной причиной
      (`Unsupported dialect: greenplum` + `PostgreSQL AST analysis failed`).
- [x] Повреждённый SQL (`CREATE VIEW demo.v AS SELECT FROM;`) сохраняет
      блокирующие coverage notes и не получает ready.
- [x] Удаление `coverage_notes` из сохранённого файла не обходит повторный анализ
      gate (`scripts/validation_gate.py` пере-парсит исходники).
- [x] Некорректный вход (`--subjects nope.missing`) остаётся `ValueError` и
      документированным кодом 2 — не превращается в пустой успешный инвентарь.

Регрессия, закрепляющая всё вышеперечисленное:
`tests/test_q02_fallback_diagnostics.py` (после проверки 27 тестов) и
`tests/test_q01_expectations.py::CrashReproductionTests`.

## 2. Ошибки старой документации (D01–D12)

Старая страница и старый аудит находятся во **внешнем** проекте `test-sql-wiki`
и остаются контрольными входами (см. план, раздел 2). Здесь ошибки 1–2 плана
закреплены воспроизводимыми компактными случаями и независимыми ожиданиями:

| Ошибка ревью | Случай | Что закреплено в ожиданиях |
|---|---|---|
| D01 `p_retro` «не используется» | q01 | связка `p_retro → v_retro → IF`; ретро-пара DELETE/INSERT; граница `fact_start_date < 2026-01-01` |
| D02 обратный знак NULL-проверки | q02 | `IS NOT NULL` и `IS NOT TRUE`; отклоняемые `IS NULL` / `IS TRUE` |
| D03 подмена наборов статусов | q03 | набор из 5 кодов против набора из 3; `ORDER BY priority DESC, created_at` + LIMIT 1 |
| D04 потерянный переход формулы | q04 | две ветви до/после `2026-01-01`, делители 8.0 и 7.2, привязка к `p_date`/`on_date` |
| D05 потерянный `EXECUTE ON MASTER` | q05 | атрибут объявления после тела; запрет трактовать его как динамический EXECUTE |
| D06 «5 строк filters» | q07 | один `xmlagg`; пять вызовов `q_meta.field_defs` — метаданные, не filters |
| D07 неверные счётчики | q09 | явные пары 10/100 и 20/200, уровни 1,2 / 1; 3 расчётные ветви, число строк данных неизвестно |
| D08 потерянные формулы | q04, q09, q10, q11 | `/8.0`, `/7.2`, `*1.5`, `+10`, RR, SL |
| D09 потерянный источник/условие | q07, q10 | `q_meta.field_defs`, `q_src.hours`, `q_src.plan_variants` и их WHERE обязательны |
| D10 неверное число колонок/тип | q08 | позиционный INSERT: 4 колонки `id, amount, legacy, total` после миграции 014 |
| D11 `tm` «это CTE» / битая ссылка | q06 | `tm` — CTE (2 области), `s` — производная таблица; рёбра reads → op → writes |
| D12 неподтверждённая совместимость | q05 | версия `unknown`; `PostgreSQL >=9.4` / `Greenplum >=5` не подтверждены |

Набор из 35 утверждений, из них **35 blocking**; D01–D12 представлены каждый
минимум одним утверждением. Расшифровка RR в q11 — дополнительный контроль
неизвестности, а не воспроизведение D12. Заготовки матрицы —
`examples/expected/qNN/assertions.json` + `examples/expected/qNN/page_assertions.json`.
Проверка всех мутаций на результате генерации ещё не выполнена (Q-07/Q-08).

## 3. Независимые ожидания

- Фикстуры: `examples/fixtures/` (11 SQL + 2 DDL-контекста + миграция + README).
- Манифест: `examples/cases-q.json` (отдельный от `cases.json`, чтобы исторический
  набор 01–12 не регрессировал до Q-08).
- Оракулы: `examples/expected/qNN/{facts,checks,decision,page_assertions,assertions}.json`.
- Авторство: `examples/expected/_authoring.py` — рукописные константы, выведенные
  **чтением SQL**, не запуском extractor/writer. Генератору (`isolate()` в
  `scripts/run_regression.py`) ожидания недоступны — проверено тестом
  `ManifestTests.test_expectations_are_not_reachable_from_adapter_inputs`.
- Положительные контроли: 10 из 11 случаев ожидают `ready`; отрицательный
  контроль q05 ожидает `blocked`. «Всегда blocked» не засчитывается.

## 4. Локальный приёмочный сценарий большого SQL

[examples/fixtures/acceptance-large.json](../examples/fixtures/acceptance-large.json):

- SHA-256 входа: `168dc68069a244fa957931d092b93c44a8f01a0b0b3bd3538a6de507cb4941c3`
- SHA-256 проверенной страницы: `5b165d85282a3356afc326263ae04b832590c960eb1e0c97af025e26314873d3`
- Политика хеша: `refuse_to_apply_numbers` — при другом хеше прежние числа
  (77 INSERT / 77 DELETE / 1 UPDATE; 36 KPI; 68 пар; 30 источников; 3 ретро-пары)
  не применять молча.
- Перечень D01–D12 сохранён в поле `known_defects_in_reviewed_page`.

## 5. Проверки этого шага

| Команда | Каталог | Результат |
|---|---|---|
| `python -B -m unittest discover -s tests -p "test_q0*.py"` | wiki-doc/wiki-doc | 50 passed |
| `python -B -m unittest discover -s tests -p "test_sql_ast.py"` | wiki-doc/wiki-doc | 20 passed |
| `python -B -m unittest discover -s tests -p "test_dialect_matrix.py"` | wiki-doc/wiki-doc | 38 passed |
| `python -B -m unittest discover -s tests -p "test_inventory_plan.py"` | wiki-doc/wiki-doc | 22 passed |
| `python -B -m unittest discover -s tests -p "test_review_fixes.py"` | wiki-doc/wiki-doc | 41 passed |
| `python -B -m unittest discover -s tests -p "test_p0_gate_regressions.py"` | wiki-doc/wiki-doc | 36 passed, 1 skipped |
| `python -B -m unittest discover -s tests -p "test_artifact_regressions.py"` | wiki-doc/wiki-doc | 49 passed |

В предыдущей сессии unit — да; `reference`/`saved` — нет; настоящий LLM-прогон —
нет. Q-01 остаётся `in_progress`: ещё нужен воспроизводимый комплект проверок
старой сохранённой страницы, не только ожидания компактных SQL. Новый полный
LLM-цикл относится отдельно к Q-08. Актуальные проверки — в отчёте по ссылке выше.
