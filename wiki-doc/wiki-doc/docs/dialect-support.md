# Матрица поддержки диалектов

Дата проверки: 2026-09-19. Матрица описывает статический анализ закреплённым
`pglast==7.14` (libpg_query) и подтверждённые тестами границы пакета.
SQL в СУБД не выполнялся. Прохождение разбора не подтверждает совместимость
с любой версией PostgreSQL или полноту семантического анализа.

## 1. Общие принципы

1. **Диалект не переключает парсер.** Для SQL-инвентаря и миграций принимаются
   `postgres` / `postgresql` без учёта регистра. Другие значения дают блокирующий
   `Unsupported dialect` либо статус миграций `unsupported`.
2. **Проверка версии ограничена MERGE.** Прямой MERGE и MERGE в разобранном
   шаблоне `EXECUTE` требуют числовую старшую версию >= 15. Отсутствие отдельной
   проверки версии у других конструкций не означает их доступность во всех версиях.
3. **`unknown` сохраняет неопределённость.** Без MERGE неизвестная версия сама
   по себе не блокирует разобранный поднабор. При MERGE она оставляет блокирующий
   пробел анализа. Поддержка новых вариантов синтаксиса MERGE отдельно не заявляется.
4. **Ограничение анализа блокирует допуск.** Gate повторно разбирает исходный SQL;
   удаление `coverage_notes` из сохранённого инвентаря не снимает блокировку.
   Неразобранный AST не получает допуск через regex fallback.
5. **CKR_GP — профиль правил проекта.** Он не выбирает диалект и не устанавливает
   версию Greenplum. Даже PostgreSQL-совместимый SQL с `dialect='greenplum'`
   блокируется. Подмена диалекта на `postgres` не подтверждает поддержку Greenplum.

## 2. Матрица поддержки

### 2.1 PostgreSQL: проверенный поднабор

В таблице 25 групп конструкций. «Поднабор» означает только перечисленный результат
на указанных сценариях. Во всех строках, кроме MERGE, проверки выполнены с
метаданными `version='15'`; пример 11 использует `unknown`. Это не матрица запусков
на серверах разных версий.

Обозначения тестов: **D** — [test_dialect_matrix.py](../tests/test_dialect_matrix.py),
**A** — [test_sql_ast.py](../tests/test_sql_ast.py),
**E** — [test_p2_03_acceptance.py](../tests/test_p2_03_acceptance.py),
**M** — [test_p2_03_model_extensions.py](../tests/test_p2_03_model_extensions.py).
Номера примеров соответствуют [cases.json](../examples/cases.json).

| Конструкция | Поддержка | Автоматический сценарий | Проверенный результат / граница |
|---|---|---|---|
| FUNCTION / PROCEDURE | Поднабор | 01, 05, 06; D `test_procedure_and_call`; A `test_returns_and_parameter_defaults` | Идентичность, параметры, RETURNS, DEFAULT; SQL/PLpgSQL-тела |
| SELECT | Поднабор | 01, 03, 09, 10; A `test_all_examples_parse_without_hidden_gaps` | reads, выражения, фильтры |
| PERFORM | Поднабор | 04, 11; A `test_two_performs_and_merge_using` | Отдельные вхождения и calls |
| CALL | Поднабор | D `test_procedure_and_call` | Имя вызываемой процедуры и обязательство операции |
| INSERT | Поднабор | 01; D `test_all_examples_parse_cleanly` | Цель и колонки |
| UPDATE FROM | Поднабор | 04; A `test_update_from_and_delete_using` | Цель отдельно от источника |
| DELETE USING | Поднабор | A `test_update_from_and_delete_using` | USING-источник не становится целью записи |
| MERGE | Поднабор, версия >= 15 | 04; D `MergeVersionGatingTests`, `GateDialectTests` | USING, условие и ветви; 15/16/17 разрешены, 12/13/14/unknown блокируют анализ; EXECUTE также проверяет версию |
| CTE | Поднабор | 03, 10; A `test_scoped_ctes_match_references` | Локальные ссылки, область, порядок; рекурсивный lineage блокируется |
| TEMP TABLE | Поднабор | 03; A `test_temp_ctas_and_view_cte_output` | Локальный scope, reference, lifetime/ON COMMIT |
| VIEW | Поднабор | 09; A `test_temp_ctas_and_view_cte_output` | Выходные имена/выражения и источники |
| MATERIALIZED VIEW | Поднабор | D `test_materialized_view_outputs_and_dependencies` | Отдельный kind, выходные колонки, источник; REFRESH не заявлен |
| CTAS | Поднабор | 10; A `test_ctas_select_result_reaches_created_table` | Создание цели и связь результата SELECT |
| PL/pgSQL IF/ELSE | Поднабор | 05; A `test_if_and_assignment_not_lost` | Условие и ветви |
| PL/pgSQL ASSIGN/INTO | Поднабор | 05; A `test_cte_result_and_into_assignment_lineage` | Цели присваивания и выражения |
| PL/pgSQL RETURN | Поднабор | 01, 04, 11; D `test_all_examples_parse_cleanly` | Видимые RETURN; произвольные варианты PL/pgSQL не заявлены |
| EXECUTE constant | Поднабор | D `test_constant_execute_retains_template_and_source` | Шаблон, тип команды, статический источник; полный анализ writes/выражений команды не заявлен |
| EXECUTE format | Поднабор | 07; A `test_dynamic_static_source_but_unknown_target` | Шаблон, аргументы, статические reads; runtime-имя остаётся неизвестным |
| TRIGGER | Поднабор | 12; M `TriggerExtractionTests`; E `test_trigger_when_and_update_columns_survive` | Имя, таблица, timing/events, row, функция, WHEN/UPDATE OF, DDL |
| INDEX | Поднабор | 12; M `IndexExtractionTests`; E `test_partial_expression_index_keeps_ast_details_inside_routine` | Имя, таблица, UNIQUE, метод, выражения/колонки, WHERE, DDL |
| CONSTRAINT (CREATE) | Поднабор | 12; E `test_inline_constraints_each_have_an_obligation` | Ограничения таблицы/колонок и отдельные обязательства |
| CONSTRAINT (ALTER ADD) | Поднабор | 12; M `test_alter_table_add_constraint_ast` | Структура и DDL; USING INDEX блокируется |
| GRANT / REVOKE | Явные relations | 12; M `GrantRevokeExtractionTests`; E `test_public_and_grant_option_are_not_roles` | Привилегии, колонки, роли, grant option; schema/routines/ALL IN SCHEMA блокируются |
| COMMENT ON | Каталог миграций | D `test_column_comment_in_ordered_migration` | Комментарий колонки в восстановленном состоянии; отдельное обязательство COMMENT для выбранной таблицы не гарантируется |
| CREATE/ALTER/DROP/RENAME | Каталог таблиц, поднабор | 08; A `MigrationTests` | Состояние по явному manifest, типы/DEFAULT/порядок; остальные действия могут дать `unsupported` |

### 2.2 Greenplum и профиль CKR_GP

| Вход | Результат | Проверка |
|---|---|---|
| `dialect='greenplum'`, обычный PostgreSQL SQL | Блокируется: неподдержанный диалект | D `test_greenplum_dialect_creates_note`, `test_unsupported_dialect_in_inventory_blocks_gate` |
| `DISTRIBUTED BY` | Ошибка разбора libpg_query и блокирующий coverage_note | D `test_greenplum_specific_syntax_blocks_analysis`, с `greenplum` и `postgres` |
| `EXECUTE ON MASTER` | Ошибка разбора libpg_query и блокирующий coverage_note | Тот же тест, оба значения диалекта |
| Профиль CKR_GP, SQL примера 11, `postgres/unknown` | Разбирается; профиль проверяет правила доступа | 11; D `test_case_11_unknown_version` |

Последняя строка не подтверждает диалект Greenplum. Его версии, MERGE,
`CREATE EXTERNAL TABLE`, другие GP-конструкции и семантика системных каталогов
не проверены. Квалифицированное имя `pg_catalog.pg_stat_*` само по себе не
имеет специальной блокировки или отдельного GP-анализатора.

### 2.3 Другие диалекты

MySQL/MariaDB, MS SQL (T-SQL), Oracle (PL/SQL), SQLite и неизвестные имена
не поддерживаются. Отдельных парсеров нет. На синтаксически допустимом PostgreSQL
SQL отказ по метаданным проверяет D `test_unknown_dialect_creates_note`;
полные комплекты проверяет `test_unsupported_dialect_in_inventory_blocks_gate`.
Чужой синтаксис может раньше привести к диагностике сбоя PostgreSQL AST.

## 3. Версия и миграции

Проверка в [sql_ast.py](../scripts/sql_ast.py) использует старшую часть строки
версии: числовое значение >= 15 пропускает проверку MERGE; `14`, `unknown`
и ненумерованные значения блокируют её. Это одно правило для прямой операции
и разобранного шаблона динамической команды. Разрешение этой проверки не
отменяет остальные проверки gate.

[ddl.py](../scripts/ddl.py) принимает оба имени PostgreSQL без учёта регистра,
сохраняет явный порядок миграций и не использует mtime. Другой диалект возвращает
`unsupported` до чтения SQL. Версия manifest хранится как метаданные; реконструктор
не является общей проверкой совместимости DDL с серверной версией.
Тесты миграций используют временные копии SQL и manifest, не меняя примеры пакета.

## 4. Автоматические сценарии

Все 12 записей в `examples/cases.json` не задают `dialect`: применяется `postgres`.
Версия равна `15`, кроме примера 11 (`unknown`).

D `test_all_examples_parse_cleanly` проверяет наличие объявления, метаданные
и отсутствие `coverage_notes` у каждого файла. Это проверка извлечения; контекст,
профиль, реконструкция миграций и полные комплекты проверяются общим
[регрессионным циклом](../references/regression.md).

`GateDialectTests` использует настоящие SQL и корректные по схеме артефакты:

- поддержанный комплект и MERGE при версии 15 получают `ready`;
- неизвестная версия без MERGE сохраняет положительный допуск;
- неподдержанный диалект и MERGE при 14/unknown дают `blocked`,
  `publication_authorized=false`, `input_error=false` и конкретный `analysis gap`;
- MERGE в константе EXECUTE и format проходит тот же контроль версии;
- удаление диагностики из inventory не обходит повторный разбор gate.

Эти проверки не заменяют содержательную валидацию произвольного текста.

## 5. Ограничения и непроверенные области

| Область | Текущее поведение / граница доказательства |
|---|---|
| Диалект Greenplum и прочие диалекты | Блокируются по метаданным; совместимость не заявлена |
| Версии PostgreSQL кроме условия MERGE >= 15 | Нет общей таблицы минимальных версий; серверные испытания не проводились |
| Новые варианты MERGE | Принимаемый pglast синтаксис не означает поддержку всех вариантов в объявленной версии |
| Рекурсивный CTE lineage | Блокирующая диагностика; A `test_recursive_cte_keeps_gap` |
| Циклы/exception handlers PL/pgSQL | Неподдержанный control flow оставляет coverage_note; цикл проверен A `test_unknown_plpgsql_keeps_gap` |
| ADD CONSTRAINT USING INDEX | Блокируется до разрешения колонок индекса; E `test_using_index_does_not_invent_primary_key_column_properties` |
| GRANT на schema/routines/ALL IN SCHEMA | Блокируется; E `test_unsupported_access_targets_remain_analysis_gaps` |
| Неоднозначные имена/search_path | Не разрешаются по догадке; неизвестный unqualified call проверен A `test_unresolved_call_not_assumed_builtin` |
| Wildcard-выходы без развёртки | Пробел анализа; полная DDL-развёртка не заявлена |
| EXECUTE | Поддержаны константа/format с одним разбираемым шаблоном; полный анализ команды и произвольных выражений не заявлен |
| Runtime-имена и параметры | Обоснованный unknown может пройти; A `test_dynamic_static_source_but_unknown_target` |
| Произвольные перегрузки, системные каталоги и runtime-эффекты | Полная семантика и проверка типов без БД не подтверждены |
| COMMENT вне каталога миграций | Извлечение/привязка всех комментариев к странице не подтверждены |

## 6. Расширение матрицы

Для новой конструкции или диалекта нужны реализация, положительные и отрицательные
тесты на реальном SQL, правила обязательств и соответствующее обновление этой
матрицы и [sql-support.md](../references/sql-support.md). Диалект требует парсера
или адаптера; добавления строки в список допустимых значений недостаточно.
Изменения проверяются общим регрессионным прогоном. При отсутствии доказательства
сохраняются явное ограничение и блокировка пробела анализа.
