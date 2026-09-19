# Контрольные примеры wiki-doc

Набор проверяет факты и решения агента. PostgreSQL-примеры 01–10 и 12–13 используют
синтаксис PostgreSQL 15+; пример 11 дополнительно проверяет проектные правила CKR_GP,
но не заявляет совместимость с неизвестной версией Greenplum.

SQL предназначен для статического документирования. Не запускай его в рабочей БД:
в примерах есть изменения структуры/данных, а некоторые зависимости намеренно отсутствуют.
Полный цикл генерации проверяется без исполнения SQL.

Исполняемый manifest: [cases.json](cases.json). Машиночитаемые ожидания и мутации
находятся в `expected/01`…`expected/13`; они доступны только проверяющей стороне.
Команды трёх повторений, saved/reference/agent-режимы и контракт адаптера:
[regression.md](../references/regression.md). Reference + независимый содержательный
проход обозначается полуавтоматическим циклом, а не полным запуском LLM writer.

## Набор

| Вход | Контекст | Что проверяется |
|------|----------|----------------|
| [01](01_no_target_ddl.sql) / [ожидания](01_no_target_ddl.expected.md) | [context.sql](context.sql) | DDL цели отсутствует, unknown вместо выдуманного типа |
| [02](02_now_and_casts.sql) / [ожидания](02_now_and_casts.expected.md) | context.sql | Разные типы цели и выражения |
| [03](03_cte_temp.sql) / [ожидания](03_cte_temp.expected.md) | context.sql | CTE, temp и фильтры |
| [04](04_update_perform.sql) / [ожидания](04_update_perform.expected.md) | context.sql | UPDATE, PERFORM, MERGE USING и диаграмма |
| [05](05_default_string.sql) / [ожидания](05_default_string.expected.md) | context.sql | Обязательные аргументы и специальная строка |
| [06](06_same_name_diff_schema.sql) / [ожидания](06_same_name_diff_schema.expected.md) | context.sql | Разные схемы и перегрузки |
| [07](07_dynamic_sql.sql) / [ожидания](07_dynamic_sql.expected.md) | context.sql | Подтверждённый шаблон динамических целей |
| [08](08_alter_migration.sql) / [ожидания](08_alter_migration.expected.md) | [manifest](migrations/manifest.json) | Порядок DROP/RENAME/TYPE/DEFAULT |
| [09](09_view_and_readonly.sql) / [ожидания](09_view_and_readonly.expected.md) | context.sql | Формулы во view и read-only функции |
| [10](10_ctas.sql) / [ожидания](10_ctas.expected.md) | context.sql | CTAS сам определяет выходную структуру |
| [11](11_audit_access.sql) / [ожидания](11_audit_access.expected.md) | Профиль CKR_GP | Прямой audit-вызов и отдельное чтение |
| [12](12_matview_index_grant.sql) / [ожидания](12_matview_index_grant.expected.md) | context.sql | Materialized view, индексы и GRANT |
| [13](13_trigger_audit.sql) / [ожидания](13_trigger_audit.expected.md) | context.sql | Триггер, CHECK constraint, GRANT/REVOKE |

Каждый случай анализируется изолированно: передавай агенту только его SQL и указанный
контекст. Другие примеры не являются определениями его объектов. Для случая 08 пути
в manifest разрешаются относительно manifest, порядок указан явно.

## Проверка генерации

1. Подготовь отдельную временную директорию SQL-проекта и wiki для случая.
2. Передай агенту запрос «Документируй <объект> по этим исходникам с помощью wiki-doc».
   Передай инструкции скилла и сырой SQL/контекст, **не передавай expected.md**.
3. Проверь артефакты facts.json, coverage.json, page.draft.md и validation.json,
   а также результат публикации в изолированную wiki. Для просмотра промежуточных
   результатов попроси сохранить их как тестовые артефакты.
4. Сверь результат с expected.md по фактам, не по точным формулировкам и числу заголовков.
5. Для проверки валидатора искази один факт в копии страницы (тип, MERGE-цель или
   умножение во view) и повтори валидацию: нужен blocking defect и отсутствие публикации.

Проверяй также отказ публикации при изменившемся исходнике/основной странице и
сохранение независимых черновиков для двух run_id. Повторный запрос на обновление
той же страницы не должен требовать подтверждения только из-за существования файла.

## Проверка численного критерия

Из корня скилла:

```text
python -m pip install -r requirements.txt
python -B -m unittest discover -s tests -v
python scripts/artifact_schema.py <run_dir> --artifacts facts coverage validation
python scripts/validation_gate.py <validation.json>
```

Тесты проверяют расчёт критерия и контракты/связность P0-01, включая отрицательные
случаи и CLI. Готовые комплекты находятся в [tests/fixtures/artifacts](../tests/fixtures/artifacts).
Проверка частичного этапа не требует ещё не созданных manifest/decision. Для полного
комплекта убери `--artifacts`; подробности — в [references/artifacts.md](../references/artifacts.md).

Вызов `validation_gate.py <validation.json>` — legacy-диагностика без допуска и
без успешного кода 0. Полный gate вызывается с `--bundle <run_dir>` и корнями
project/wiki; только он проверяет независимую полноту, доказательства и готовность
комплекта к публикации. Приведённые проверки не исполняют SQL и не заменяют
описанный выше содержательный прогон 13 сценариев.
