# Матрица поддержки диалектов

Статус выводится из SQL, автоматических проверок и границ анализатора.
Наличие обработчика в коде само по себе не подтверждает поддержку конструкции.
Проверки статические: SQL не исполняется на сервере СУБД.

## Обзор

| Диалект / профиль | Что подтверждено | Ограничения |
|---|---|---|
| PostgreSQL (`postgres`, `postgresql`) | PostgreSQL-разбор всех 13 сценариев; 12 без профиля CKR_GP, один с ним | Только проверенный поднабор; совместимость с серверными версиями не проверена |
| Greenplum / CKR_GP | Сценарий 11: правила доступа CKR_GP, PostgreSQL-синтаксис, `version: "unknown"` | Отдельного Greenplum-парсера нет; имя диалекта `greenplum` отклоняется |
| MySQL, MSSQL, Oracle, SQLite, неизвестный диалект | Проверен отказ в допуске | Положительных сценариев нет; требуется отдельная адаптация |

Профиль CKR_GP добавляет соглашения проекта, а не поддержку диалекта.
Он не включается только из-за присутствия в пакете и не определяет версию Greenplum.

## PostgreSQL

### Версия в артефактах и версия парсера

В [cases.json](../examples/cases.json) 12 случаев задают `version: "15"`,
а случай 11 — `version: "unknown"`. Все используют `dialect.name: "postgres"`.
Это метаданные статического анализа, а не результаты запуска SQL на PostgreSQL 15
или Greenplum. `version` не переключает грамматику парсера; анализатор, например,
отдельно требует подтверждённую версию PostgreSQL ≥15 для MERGE.

Парсер закреплён как `pglast==7.14` в [requirements.txt](../requirements.txt).
В проверенном окружении `pglast.get_postgresql_version()` возвращает `(17, 7)`.
Регрессионных запусков на серверах PostgreSQL или Greenplum нет; поддержка всех
конструкций какой-либо серверной версии не заявляется.

### Конструкции и доказательства

Номера ниже относятся к [контрольным сценариям](../examples/cases.json).
Имена тестов — к [test_sql_ast.py](../tests/test_sql_ast.py), если не указано иное.
Сквозной reference-цикл проверяет только выбранные `subjects` каждого случая.
Отдельный тест разбора не равен проверке полного цикла публикации.

| Конструкция | Подтверждение | Граница проверки |
|---|---|---|
| FUNCTION | 01, 03–07, 09, 11 | SQL/PLpgSQL, параметры, DEFAULT, RETURNS, перегрузки; отдельного SQL-сценария PROCEDURE пока нет |
| SELECT / PERFORM | 01, 03, 04, 09, 11 | Выражения, фильтры, reads/calls; отдельного теста SQL-команды CALL пока нет |
| INSERT | 01, 02, 03 | Цель, колонки, выражения; отсутствие DDL описывается явно |
| UPDATE / MERGE | 04 | UPDATE с WHERE; MERGE с USING и отдельными ветвями UPDATE/INSERT |
| UPDATE FROM / DELETE USING | `test_update_from_and_delete_using` | Отдельный тест инвентаря; этих конструкций в сценарии 04 нет |
| CTE / TEMP TABLE | 03; `test_scoped_ctes_match_references`, `test_temp_ctas_and_view_cte_output` | Область видимости, локальные зависимости, ON COMMIT |
| VIEW / MATERIALIZED VIEW / CTAS | 09, 10, 12 | Выходные имена/выражения, создание и заполнение |
| IF / ASSIGN / INTO / RETURN | 03, 05, 11; `test_if_and_assignment_not_lost`, `test_cte_result_and_into_assignment_lineage` | Проверенные ветвления и присваивания; циклы/exception handlers не покрыты |
| EXECUTE constant / format | 07; `test_dynamic_static_source_but_unknown_target` | Шаблон разобран; конкретная runtime-цель может остаться неизвестной |
| CREATE / ALTER / RENAME / DEFAULT / COMMENT | 08; `MigrationTests` | Табличное состояние по manifest; порядок не выводится из mtime |
| DROP / финальное состояние ALTER | [test_p2_model_review.py](../tests/test_p2_model_review.py): `test_drop_uses_object_list_and_removes_final_table`, `test_alter_final_column_state_survives_build_and_gate` | DROP проверен на catalog; ALTER/PK/NOT NULL — также через build/gate |
| CREATE INDEX | 12; `test_matview_with_index_and_grant` | Два индекса, unique/non-unique, колонки, предикат; в сценарии 13 индекса нет |
| CREATE TRIGGER | 13; `test_create_trigger_inline`; [test_p2_model_review.py](../tests/test_p2_model_review.py): `test_instead_of_trigger_on_view_has_correct_scope` | AFTER проверен сквозным build, BEFORE и INSTEAD OF — тестами инвентаря |
| GRANT / REVOKE | 12, 13; [test_p2_model_review.py](../tests/test_p2_model_review.py): `test_column_grant_preserves_column_scope`, `test_shared_grant_is_present_in_each_target_scope` | Таблицы/view, права, PUBLIC, списки колонок; в 11 нет GRANT/REVOKE, grant option отдельным тестом не подтверждён |
| ALTER ADD CONSTRAINT | 13; `test_alter_add_foreign_key`; [test_p2_model_review.py](../tests/test_p2_model_review.py): `test_primary_unique_foreign_constraints_keep_keys` | CHECK/FK/PK/UNIQUE; NOT NULL проверяется отдельно как свойство колонок |

### Пробелы анализа и ограничения

| Случай | Фактическое поведение |
|---|---|
| Циклы и exception handlers PL/pgSQL | Обработчики анализатора не устанавливают полный control flow; `coverage_notes` блокируют gate |
| Рекурсивный lineage CTE | `coverage_notes` блокируют gate; проверено `test_recursive_cte_keeps_gap` |
| Wildcard-выходы без развёртки | Требуется развёртка по DDL; `coverage_notes` блокируют gate |
| Необработанный AST-узел или ошибка разбора | Блокирующий пробел; regex-сканер не разрешает публикацию после ошибки AST |
| Неразрешённая схема/search_path для объекта, reads/writes или типа объявления | `coverage_notes` блокируют gate |
| EXECUTE с неразобранным выражением/шаблоном | Блокирующий пробел; `test_unanalyzed_dynamic_sql_blocks_gate` проверяет полный gate |
| Разобранный EXECUTE с неизвестным runtime-именем | Допустимо честное `unknown`; сценарий 07 проходит gate |
| GRANT на схемы/functions, ALL TABLES IN SCHEMA | Неподдержанные цели оставляют блокирующий пробел |
| INDEX/TRIGGER/GRANT внутри migration manifest | `ddl.reconstruct` возвращает `unsupported`; извлечение отдельных операций не доказывает реконструкцию миграций |
| Произвольные вложенные запросы и новые сочетания конструкций | Обобщённая полнота не подтверждена; нужны отдельные сценарии и мутации |

Детали инвентаря, типов, выбора страницы и реконструкции:
[references/sql-support.md](../references/sql-support.md).

## Greenplum и профиль CKR_GP

Сценарий 11 проверяет **одну страницу** `audit_probe`: вызов audit-функции
отличается от прямого чтения audit-таблицы на этой же странице. Он не создаёт
отдельных страниц для каждого действия. Полный build/gate и сохранение
`{"name": "postgres", "version": "unknown"}` проверяет
`test_ckr_gp_example_uses_postgres_parser_with_unknown_version`.

Это подтверждение PostgreSQL-поднабора и правил профиля, а не совместимости
с конкретной версией Greenplum. Явный `dialect=greenplum` оставляет
`Unsupported dialect: greenplum` и блокирует gate; в migration manifest
он даёт `status: "unsupported"`. Нельзя скрывать неподдержанный диалект,
меняя его название ради допуска.

`DISTRIBUTED BY` и `DISTRIBUTED RANDOMLY` проверены отрицательным тестом
`test_greenplum_distribution_is_an_analysis_gap`: даже при PostgreSQL-режиме
ошибка AST остаётся блокирующей. `EXECUTE ON`, `CREATE EXTERNAL TABLE`,
управление ресурсами и storage options (`APPENDONLY`, `ORIENTATION`,
`COMPRESSTYPE`) не имеют положительных сценариев и не поддержаны этой матрицей.
Нельзя утверждать, что каждый такой вариант обязательно отвергается синтаксически:
общая форма SQL-опций может разбираться без проверки специфичной семантики.

Версию берут только из проекта, иначе сохраняют `unknown`. Профиль
[CKR_GP](../profiles/ckr_gp/profile.md) не расширяет грамматику и не заменяет
проверку диалекта/версии.

## Другие диалекты и тесты отказа

`DialectRejectionTests` в [test_sql_ast.py](../tests/test_sql_ast.py) проверяет
MySQL, MSSQL, Oracle, SQLite, `greenplum` и `unknown` на корректном SQL,
чтобы отказ нельзя было объяснить посторонней синтаксической ошибкой:

- основной `extract_inventory`, прямой AST и старый regex-сканер сохраняют
  `Unsupported dialect: ...`;
- реконструкция миграций возвращает `unsupported`;
- полный gate возвращает `blocked` и `publication_authorized: false`, даже
  если согласованные артефакты сопровождаются отчётом со всеми статусами `ok`;
- `postgres` и `postgresql` дают инвентарь выбранной таблицы без пробелов.

Положительные сценарии для других СУБД отсутствуют. Для адаптации нужны парсер,
типизация/идентичность, правила плана, контрольные SQL и негативные мутации.

## Как воспроизвести и обновлять

Запуск из корня пакета после установки [зависимостей](../requirements.txt):

```text
python -B -m pytest tests/test_sql_ast.py tests/test_p2_model_review.py -q -p no:cacheprovider
```

Общий набор из [cases.json](../examples/cases.json) содержит 13 случаев и
17 выбранных предметов страниц. Reference build → gate → publish выполняется
по [регрессионному протоколу](../references/regression.md); это полуавтоматический
цикл с детерминированным адаптером, не полный прогон LLM и не исполнение SQL в БД.
Общее число тестов контрактов, Markdown или идентичности не является числом
проверок совместимости диалекта.

Добавляя подтверждённую конструкцию или диалект, добавь SQL-сценарий,
ожидаемые факты, полный build/gate и негативные мутации. Обнови этот файл и
[sql-support.md](../references/sql-support.md), сохраняя различие между тестом
инвентаря, сквозным циклом и непроверенной совместимостью.
