# Актуальный статус — 2026-09-19

**P2-05 повторно проверен и исправлен.** Все пункты P0–P2 закрыты в заявленном
объёме. Исправлены передача контекста/run_id/миграций, выбор предметов и профиля,
публикация с явной политикой, identity и ошибки входа. Добавлены prepare/recover
и журнал этапов, восстановлена полнота изолированного пакета, уточнены инструкции
и шаблоны. CLI: 30 тестов, включая 16 новых регрессионных.
Приёмка: [REVIEW-P2-05.md](REVIEW-P2-05.md), [P2-05-ACCEPTANCE.json](P2-05-ACCEPTANCE.json).

**P2-04 повторно проверен и исправлен.** Матрица диалектов теперь различает
PostgreSQL-парсер, серверную версию и профиль CKR_GP; исправлены границы EXECUTE,
миграций и привязки конструкций к тестам. Изолированный адаптер получает матрицу.
Добавлены 6 тестов к 7 первоначальным тестам P2-04; усилены проверки отказа полного
gate и изоляции. Исправлен сбой конкурентной публикации на Windows, добавлены
2 теста путей publisher. Приёмка: [REVIEW-P2-04.md](REVIEW-P2-04.md),
[P2-04-ACCEPTANCE.json](P2-04-ACCEPTANCE.json).

**P2-03 повторно проверен и исправлен в поддержанном подмножестве.** Устранены
пропуски неизвестных операторов, смешение DDL соседних объектов, сбой DROP,
потеря финального состояния ALTER/PK и межфайловых конфликтов, неверная маска
INSTEAD OF. Типизированы структуры facts v2 и сохранены поколоночные права.
Пример 13 разделён на страницы audit_log и orders с триггером. Подробности:
[REVIEW-P2-03.md](REVIEW-P2-03.md), [P2-03-ACCEPTANCE.json](P2-03-ACCEPTANCE.json).
Регрессия: 13 случаев, 17 предметов × 3 повтора; 38 отклонённых мутаций,
17 публикаций с повтором без дублей и чистым lint.

**Текущий полный набор: 662 теста — 661 passed, 1 skipped, 196 subtests passed.**
Пропуск — symlink на Windows. 51 тест метрик, 43 теста SQL AST,
15 дополнительных тестов приёмки P2-03.

**P1-01…P1-07 выполнены и проверены.** Предыдущие исправления P0 сохранены.
Подробная приёмка: [REVIEW-P1-COMPLETE.md](REVIEW-P1-COMPLETE.md),
машиночитаемые результаты: [P1-ACCEPTANCE.json](P1-ACCEPTANCE.json).
Эта запись заменяет промежуточные статусы и числа тестов в истории ниже.

| Пункт | Итог |
|---|---|
| P1-01 | Каноническая идентичность, сигнатуры, прежние пути и коллизии интегрированы с gate |
| P1-02 | CommonMark, покрытие, секции/фрагменты и локальные ссылки, включая каталоги |
| P1-03 | AST PostgreSQL/PLpgSQL, предметные операции/связи/типы и реконструкция миграций по manifest |
| P1-04 | 11 случаев, 14 предметов, 3 повтора, 28 отклонённых мутаций, независимое ревью всех страниц |
| P1-05 | Условно подключаемый CKR_GP, отдельная история, исправленные ссылки и инструкции |
| P1-06 | Read-only lint, проверка provenance/актуальности/покрытия; legacy и source findings отдельно |
| P1-07 | Публикация проверенных байтов, ручные блоки, dry-run, lock/journal/recovery, архив и безопасная очистка |

Историческая приёмка P1: **512 тестов — 511 прошли, 1 пропущен**
(создание symlink недоступно на этой Windows). Это не текущий размер набора.
Все 42 комплекта трёх прогонов получили ready, разброса фактов/решений нет.
28 негативных мутаций дали отказ; ошибочных ready — 0. В изолированную wiki реально
опубликованы 14 страниц; повтор без дублей, итоговый lint без ошибок и предупреждений.
Испытаны сбой процесса, recovery, чужая правка и конкурентные publisher.

Приёмка генерации **полуавтоматическая**: reference/saved cycle и независимый
содержательный проход. Полный LLM-адаптер не запускался. Ограничения AST/типов,
Mermaid без рендерера и пределы блокировки внешнего редактора перечислены в отчёте.
Исходная wiki сохранена; на копии проверены 11 legacy-страниц и предложены соответствия
ключей без объявления старых страниц прошедшими новый gate. Далее — отдельный этап P2.


## История проверки P0 — 2026-09-12

Заявление о полном выполнении P0 по исходному отчёту не подтвердилось: все 202 теста
проходили, но положительный тест gate допускал любой результат, а ключевые этапы
проверки не влияли на решение. Замечания исправлены в текущей рабочей копии.

| Область | Результат проверочного исправления |
|---------|-----------------------------------|
| P0-01 | Полный gate требует все артефакты и проверяет их схемы/связность, включая manifest/decision; поддержаны структурированные evidence и ссылки fact_ids |
| P0-02 | Политика применяется по умолчанию; обязательства пересчитываются, их применимость и критичность не снижаются отчётом; исправлены OR, порог источников и сверка ID секций с шаблоном |
| P0-03 | Исправлены CREATE без OR REPLACE, именованные dollar quotes, вложенные комментарии, смещения строк, выбор объявления, границы операторов, видимые вызовы и RETURN; неполный анализ блокирует gate |
| P0-04 | Проверяются реальные доказательства, границы корней после resolve, хэши кода/схем/инструкций/блоков шаблона/профиля/политики, порядок миграций; сохранение архива проверяет обе копии и сохраняет снимки SQL |
| P0-05 | Удалены обходы через неполный bundle, несверенный inventory/plan, дополнительные успехи, not_applicable и понижение blocking; проверяются покрытие, оба хэша decision, результат и метрики; legacy даёт только диагностику |

Первый полный gate вызывается с `--write-decision`, повторная проверка готового
решения — без этого флага. `ready` возможен только для поддержанного поднабора
при независимой содержательной валидации SQL → факты → страница. Ограничения
MERGE/CTE/сложного PL/pgSQL, структурного DDL, миграционного разбора, quoted identifiers
и иных диалектов оставляют блокирующие coverage_notes; расширение — P1-03.
Полный Markdown-парсер и сложные fragment_ref — P1-02. Иные профили требуют
собственного вывода обязательств; P0 поддерживает поставляемый CKR_GP.

Исправлены инструкции SKILL.md, doc-writer.md, doc-validator.md, template.md и
references/{artifacts,facts}.md. Ручной процент не заменяет машинный допуск.

Регрессии: полный положительный bundle и его повторная проверка; удаление операции
из facts/coverage/report; подмена инвентаря/плана; неполный комплект; ложные evidence;
смешение run/page; подмена решения и версий; ровно 85%; архив после удаления внешнего
SQL; профиль; разрешённый ../baseline и выход за корень; legacy и коды CLI.
Контрольный запуск: `python -B -m unittest discover -s tests -q` — **238 тестов,
237 прошли, 1 пропущен** (Windows не разрешает создание symlink в этом окружении).
Проверка skill-creator quick_validate.py — `Skill is valid!`; git diff --check — без ошибок.
Тесты сравнения корней, выхода через ../ и полного переноса архива выполнены; пропуск
symlink не означает проверку этого поведения на данной Windows-машине.

## Исторические отчёты до проверочного исправления

Записи ниже сохранены как история реализации. Их промежуточные ограничения и
заявления о завершённости заменены результатами проверочного исправления выше.

# Статус реализации wiki-doc

Дата: 2026-09-12. План: [IMPLEMENTATION-PLAN.md](IMPLEMENTATION-PLAN.md).

## P0 — исключить формальный допуск неполного результата

### P0-01. Версионированные контракты и связность артефактов

**Статус:** done — реализовано, проверено и исправлено после ревью.
**Обновлено:** 2026-09-12T12:46:43Z. **Проверил:** Codex (/root).

Первоначальные 39 тестов проходили, но не обеспечивали все критерии связности:
пустой комплект, несуществующие объекты/зависимости, дубли проверок, другой page_id
и неверная дата принимались; неверный тип ID приводил к падению. Дефекты
воспроизведены и исправлены. Подробности: [REVIEW-P0-01.md](wiki-doc/REVIEW-P0-01.md).

Выполнено:

- Восемь схем: семь артефактов запуска v2 и отдельный manifest миграций.
- Общая уникальность ID фактов, типизированные внутренние ссылки, связи
  coverage → facts, plan → inventory, report → plan, decision → report,
  пути manifest и согласованность run_id/page_id.
- Полный комплект обязателен по умолчанию. Частичный этап выбирается через
  --artifacts и помечается partial; пустой каталог не проходит.
- Диагностика ошибок JSON/UTF-8/чтения и повторных ключей без traceback.
- Проверка дат с обязательным checker; зависимости закреплены в requirements.txt.
- Отдельный CLI для manifest миграций. Доказательства двух типов колонки разделены;
  поддерживаются обоснованный unknown, локальные CTE/temp и контекстные объекты.
- Обновлены SKILL, writer, validator, references и команды примеров; описан переход.

Приёмка:

- [x] Отсутствующее поле, неверный тип/статус отклоняются с путём к полю.
- [x] Повторные ID, битые ссылки и смешение запусков отклоняются.
- [x] Срез страницы не требует полного покрытия контекстных объектов.
- [x] Старый отчёт отклоняется валидатором v2; автоматического переноса ready нет.
- [x] Валидные/невалидные комплекты сохранены в tests/fixtures/artifacts.
- [x] Инструкции, зависимости и способ установки согласованы с кодом.

**Проверки:** `python -B -m unittest discover -s tests -q` из каталога `wiki-doc/` —
**88/88 проходят**: 9 прежних, 30 исходных P0-01 и 49 регрессионных.
CLI полного/частичного комплекта и manifest миграций входят в прогон.
Проверены ссылки Markdown и quick_validate с явным UTF-8.
Логи отдельно не сохранены; результаты описаны в отчёте ревью.

**Ограничения:** P0-01 проверяет форму и объявленные связи; результат всегда
содержит publication_authorized: false. Реальные файловые доказательства — P0-04;
полный gate — P0-05. Старый validation_gate.py по-прежнему считает отдельный отчёт
и может вернуть ready, что не означает готовность комплекта. Остальные задачи
плана не объявлены выполненными.

**Блокировки:** нет в границах P0-01.
**Следующее действие:** P0-02. Перед работой прочитать актуальные схемы и
[references/artifacts.md](wiki-doc/references/artifacts.md).

### P0-02. Каталог обязательств, применимость и критичность

**Статус:** in progress — основные компоненты созданы, тесты проходят.

Выполнено:

- `references/check-policy.json` — каталог проверок версии 1:
  - 16 правил с категориями technical/editorial и дефолтным blocking.
  - 7 групп по типу объекта (table, view, materialized_view, ctas, function, procedure, migration).
  - Условия применимости по контексту (has_reads, has_writes, has_formulas и т.д.).
  - Правила применимости секций шаблона (6 блоков).
  - Правила блокировки: 11 дефектов, которые нельзя сделать редакционными.
- `schemas/check_policy.schema.json` — JSON Schema для каталога.
- `scripts/check_policy.py` — модуль:
  - `load_policy()` — загрузка и валидация каталога.
  - `derive_required_checks()` — вывод обязательных проверок по типу объекта и контексту.
  - `derive_section_checks()` — вывод проверок секций шаблона.
  - `derive_inventory_checks()` — вывод проверок из inventory.
  - `is_defect_blocking()` — определение блокировки по правилу, а не по решению агента.
  - `validate_check_against_policy()` — валидация отдельной проверки.
  - CLI: `show` (сводка) и `derive` (генерация проверок).
- `tests/test_check_policy.py` — 36 тестов: загрузка, получение правил, вывод проверок по всем типам объектов, секции, inventory, блокировка, валидация.

Приёмка:

- [ ] Неверный тип/формула/цель MERGE блокирует даже при blocking: false.
- [ ] Пропущенная операция блокирует.
- [ ] Необоснованное правило доступа блокирует.
- [ ] Допустимый редакционный дефект остаётся неблокирующим.
- [ ] Честно описанное отсутствие DDL может пройти проверку.
- [ ] Правила секций согласованы с template.md.

**Проверки:** `python -B -m unittest discover -s tests -q` — **125/125 проходят**.

**Ограничения:** P0-02 определяет каталог и механизм вывода проверок.
Интеграция с validation_gate.py и полная проверка применимости — P0-05.
Независимый inventory для проверки контекста — P0-03.

**Блокировки:** нет в границах P0-02.
**Следующее действие:** P0-03. Перед работой прочитать check-policy.json и
скрипт check_policy.py.

### P0-03. Независимый SQL-инвентарь и план проверок

**Статус:** in progress — экстрактор и генератор плана созданы, тесты проходят.

**Обновлено:** 2026-09-12. **Проверил:** opencode.

Выполнено:

- `scripts/sql_extract.py` — независимый SQL-экстрактор (regex-based):
  - Извлечение CREATE FUNCTION/PROCEDURE/VIEW/MATERIALIZED VIEW/TABLE AS.
  - DML: SELECT, INSERT, UPDATE, DELETE, MERGE.
  - PL/pgSQL: PERFORM, CALL, EXECUTE (dynamic).
  - CTE, TEMP TABLE, FROM/JOIN зависимости.
  - Dollar-quoted тела функций, strip строк и комментариев.
  - CLI: `python scripts/sql_extract.py <file.sql>`.
  - **Поддержанный поднабор:** PostgreSQL, dollar-quoted bodies, basic DML.
  - **Ограничения:** не полный AST, dynamic имена не разрешаются, сложные
    подзапросы частично, диалекты кроме PG не гарантированы.
- `scripts/validation_plan.py` — генератор плана проверок:
  - Объединяет inventory + check_policy → validation_plan.json.
  - Автоматическое определение object_kind по конструкциям.
  - Контекст: has_reads, has_writes, has_calls, has_dynamic_sql и т.д.
  - CLI: `python scripts/validation_plan.py <inventory.json> --kind function`.
- `tests/test_inventory_plan.py` — 22 теста: извлечение объектов, операций,
  inventory по 4 примерам, генерация плана, CLI.

Приёмка:

- [x] Для example 01 (INSERT+SELECT) инвентарь содержит обе операции.
- [x] Для example 04 (PERFORM+UPDATE+MERGE) инвентарь содержит все три типа.
- [x] Для example 07 (EXECUTE dynamic) инвентарь содержит EXECUTE.
- [x] Для example 09 (view+function) инвентарь содержит SELECT.
- [x] SQL-слова в комментарии не создают операций.
- [x] SQL-слова в строке не создают операций.
- [x] План содержит identity, sql_registry, registry_document для любого объекта.
- [ ] Удаление операции из facts не удаляет её из плана (требует интеграции с gate — P0-05).
- [ ] Неизвестный синтаксис оставляет блокирующий пробел (требует расширения экстрактора).

**Проверки:** `python -B -m unittest discover -s tests -q` — **147/147 проходят**.

**Ограничения:** Экстрактор покрывает основные конструкции примеров 01-10.
Для полного покрытия всех 11 примеров нужно расширение (P1-03).
Интеграция с gate для сверки plan vs facts — P0-05.

**Блокировки:** нет в границах P0-03.
**Следующее действие:** P0-04. Перед работой прочитать check-policy.json и
скрипты sql_extract.py/validation_plan.py.

### P0-04. Проверяемые доказательства и привязка к комплекту

**Статус:** in progress — модули созданы, тесты проходят.

**Обновлено:** 2026-09-12. **Проверил:** opencode.

Выполнено:

- `scripts/evidence.py` — модуль проверяемых доказательств:
  - Формат: root (project/run/wiki/package), path, start_line, end_line, sha256.
  - `validate_evidence()` — полная проверка: существование файла, принадлежность
    корню, диапазон строк, SHA-256 хэш (байты, не нормализованный текст).
  - `validate_source_ref()` / `validate_source_refs()` — валидация source_refs.
  - `check_evidence_against_inputs()` — сверка хэшей с declared inputs.
  - `check_evidence_existence()` — быстрая проверка существования.
  - Регистрация корней: `register_root()` / `get_root()` / `clear_roots()`.
  - CLI: `evidence validate <file.json> --root NAME PATH`.
- `scripts/bundle.py` — модуль привязки к комплекту:
  - `create_manifest()` — формирование manifest.json с хэшами всех файлов.
  - `compute_tool_versions()` — хэши скриптов, SKILL.md, template.md, policy, profile.
  - `verify_manifest_hashes()` — проверка хэшей файлов в manifest.
  - `verify_decision_against_manifest()` — сверка decision с manifest (хэш и run_id).
  - `reverify_bundle()` — полная повторная проверка комплекта.
  - `save_bundle()` — сохранение проверенного комплекта в долговременное хранилище.
  - CLI: `bundle create <dir> --page-id <id>` и `bundle verify <dir>`.
- `tests/test_evidence_bundle.py` — 48 тестов: хэши, строки, корни, валидация
  доказательств, создание/верификация manifest, сверка decision, CLI.

Приёмка:

- [x] Несуществующий файл отклоняется.
- [x] Неверный хэш отклоняется.
- [x] Неверный диапазон строк отклоняется.
- [x] Выход за пределы корня отклоняется.
- [x] Изменение файлов в manifest обнаруживается при верификации.
- [x] Неверный manifest_sha256 в decision обнаруживается.
- [x] Несовпадение run_id между decision и manifest обнаруживается.
- [ ] Изменение любого элемента комплекта аннулирует старое решение (требует P0-05).
- [ ] Долговременное хранение в wiki (.wiki-doc/runs/) — при публикации P1-07.

**Проверки:** `python -B -m unittest discover -s tests -q` — **195/195 проходят**.

**Ограничения:** Модули работают автономно. Интеграция с gate (P0-05) свяжет
проверку доказательств с общим циклом допуска. CLI bundle не проверяет
содержательную корректность — только форму и хэши.

**Блокировки:** нет в границах P0-04.
**Следующее действие:** P0-05. Перед работой прочитать scripts/evidence.py,
scripts/bundle.py и scripts/validation_gate.py.

### P0-05. Единый gate и негативные испытания допуска

**Статус:** in progress — gate расширен до проверки комплекта, тесты проходят.

**Обновлено:** 2026-09-12. **Проверил:** opencode.

Выполнено:

- `scripts/validation_gate.py` — расширен до bundle-режима:
  - `evaluate_bundle()` — полная проверка комплекта:
    1. Загрузка и верификация manifest (хэши файлов).
    2. Загрузка артефактов (facts, inventory, plan, coverage, validation).
    3. Валидация схем через `artifact_schema.py`.
    4. Перестроение независимого inventory из SQL.
    5. Вывод обязательных проверок из policy.
    6. Сверка сохранённого inventory/plan с перестроенным.
    7. Проверка доказательств против declared inputs.
    8. Оценка validation report.
    9. Верификация decision против manifest.
  - CLI: `--bundle <run_dir>` для bundle-режима, `--policy` для каталога.
  - Обратная совместимость: legacy-режим с одним validation.json сохранён.
  - Коды: 0 для ready, 1 для revise/blocked, 2 для ошибки входа.
- `tests/test_evidence_bundle.py` — расширен до 55 тестов:
  - Негативные тесты gate: без manifest, с corrupted manifest, с изменённым
    SQL, без validation, с неверным decision hash, с несовпадением run_id.
  - Положительный тест: валидный комплект проходит без ошибок manifest.

Приёмка:

- [x] Gate блокирует при отсутствии manifest.
- [x] Gate блокирует при corrupted manifest.
- [x] Gate блокирует при изменении SQL после manifest.
- [x] Gate блокирует при неверном manifest_sha256 в decision.
- [x] Gate блокирует при несовпадении run_id.
- [ ] Три общие проверки для сложного объекта → отказ (требует интеграции с policy).
- [ ] Удаление операции из facts без удаления из plan → отказ (требует полной интеграции).

**Проверки:** `python -B -m unittest discover -s tests -q` — **202/202 проходят**.

**Ограничения:** Gate проверяет форму и хэши. Содержательная сверка
(SQL → факты → страница) остаётся за пределами P0. Полная интеграция
с policy для проверки конкретных обязательств — P1.

**Блокировки:** нет в границах P0-05.
**Следующее действие:** P1-01. Перед работой прочитать scripts/validation_gate.py.

### P1-01. Воспроизводимая идентичность объектов

**Статус:** done — модуль создан, тесты проходят.

**Обновлено:** 2026-09-13. **Проверил:** opencode.

Выполнено:

- `scripts/identity.py` — детерминированная идентичность объектов:
  - `canonical_key()` — канонический ключ по правилам SKILL.md:
    - relation: `kind+schema+name`
    - function/procedure: `kind+schema+name+(type1,type2,...)` — всегда,
      включая ноль аргументов (пустые скобки);
    - migration: `migration+path+id`
  - `page_slug()` — безопасный slug для имени файла.
  - `page_id()` — slug + SHA-256 суффикс (12 hex по умолчанию).
  - `detect_collision()` — проверка существующих page_id.
  - `compute_identity()` — полный расчёт всех полей за один вызов.
  - CLI: `compute` и `check` подкоманды.
  - Нормализация типов: только подтверждённые алиасы PG (int4→integer,
    int8→bigint, bool→boolean, float4→real и т.д.).
  - Quoted identifiers: сохранение регистра `"MySchema"` → `MySchema`,
    unquoted → lowercase.
  - INOUT/VARIADIC включаются в ключ; OUT исключается; имена параметров
    и DEFAULT не участвуют в ключе.
  - При коллизии хэша — удлинение суффикса, без переименования существующих.
- `schemas/identity.schema.json` — JSON Schema для результата identity.
- `tests/test_identity.py` — 103 теста:
  - Нормализация идентификаторов и типов.
  - Канонические ключи для всех видов объектов.
  - Сценарий 06: три объекта (два в разных схемах + перегрузка)
    получают разные ключи и page_id.
  - Переименование аргумента не меняет ключ.
  - Коллизии: расширение хэша, прогрессивное разрешение.
  - CLI, стабильность ключей, quoted identifiers.
- Обновлены SKILL.md: команды identity.py в разделах «Идентификатор страницы»
  и «Полный цикл P0».

Приёмка:

- [x] Три объекта сценария 06 получают разные page_id.
- [x] Переименование аргумента при прежнем типе не меняет ключ.
- [x] Quoted identifiers сохраняют регистр, unquoted — lowercase.
- [x] INOUT/VARIADIC включаются, OUT исключается.
- [x] Порядок типов аргументов влияет на ключ.
- [x] Миграции: путь + ID; автоматическое извлечение ID из имени файла.
- [x] Коллизия хэша разрешается удлинением суффикса.
- [x] Неизвестный kind вызывает IdentityError.
- [x] Ключ стабилен между вызовами (100 повторов).
- [x] Все 340 тестов пакета проходят.

**Проверки:** `python -B -m pytest tests/ -q` — **340 passed, 1 skipped**
(Windows symlink). `python -B -m pytest tests/test_identity.py -v` —
103/103 проходят.

**Ограничения:** identity.py работает автономно. Интеграция с gate
(P0-05) для проверки page_id в facts.objects — отдельная задача.
Нормализация типов покрывает только подтверждённые PG-алиасы;
пользовательские типы и search_path не нормализуются.

**Блокировки:** нет в границах P1-01.
**Следующее действие:** P1-02. Перед работой прочитать scripts/identity.py
и references/facts.md.

## P1 — механизировать критические пути

| Задача | Статус |
|--------|--------|
| P1-01. Воспроизводимая идентичность объектов | done |
| P1-02. Полноценная проверка покрытия текста | done |
| P1-03. Расширение независимого SQL-анализа | done |
| P1-04. Машиночитаемые ожидания и регрессионный запуск | done |
| P1-05. Ядро, подключаемый профиль и история | done |
| P1-06. Исполняемый Lint | done |
| P1-07. Безопасная локальная публикация и восстановление | done* |

\* P1-07: реализация завершена. Subprocess-сценарии `tests/test_publish.py` и
`tests/test_index_query.py` теперь сами передают `PYTHONPATH=scripts/` дочернему
процессу, поэтому полный прогон не требует внешней настройки окружения.

## P2 — развитие после приёмки P0 и P1

| Задача | Статус |
|--------|--------|
| P2-01. Индекс, граф и Query | done |
| P2-02. Метрики | done |
| P2-03. Расширение модели | done |
| P2-04. Матрица диалектов | done |
| P2-05. Эксплуатация | done |

### P2-01. Индекс, граф и Query — текущая проверка 2026-09-14

Каталог, граф и Query исправлены согласно [REVIEW-P2-01.md](REVIEW-P2-01.md).
Пять файлов публикации входят в общий write set, явная пересборка пары — под тем же
lock с recovery. Query читает проверенные архивы и строит пару в памяти; устаревший
или повреждённый кэш не используется. Legacy без ключа, scoped CTE/temp, external,
возможные перегрузки, динамические gaps, evidence и отсутствующие источники различаются.
Проверены 14 реальных страниц, сбои, смерть процесса, чужая правка и конкуренция.
Итоговые числа полного запуска находятся в `P2-01-ACCEPTANCE.json`.

#### Исходный отчёт P2-01 от 2026-09-13 — исторический, исправлен выше

**Статус:** done — машинный индекс и граф собираются автоматически после каждой
публикации.

**Обновлено:** 2026-09-13. **Проверил:** opencode.

Выполнено:

- `schemas/index.schema.json` — машиночитаемый каталог страниц wiki
  (managed + legacy), со ссылками на SHA-256 страниц и исходников.
- `schemas/lineage.schema.json` — направленный граф reads/writes/calls.
- `scripts/index.py` — построение `index.json` и `lineage.json` из опубликованных
  комплектов; CTE/temp_table остаются scoped `@cte:<scope>` в edges и не
  становятся узлами; неразрешённые ссылки помечаются `@external:<id>`.
- `scripts/query.py` — `--page`, `--dependencies`, `--consumers`, `--affected`,
  `--outdated`; авто-пересборка артефактов при отсутствии.
- `scripts/publish.py` — после `committed` вызывает `rebuild_after_publish(root)`
  под индексной блокировкой; отказ пересборки поднимает `WikiConflict`.
- `SKILL.md` — секция Query переписана: команды и поведение для CTE/external.
- `tests/test_index_query.py` — 16 тестов:
  - managed + legacy обнаружение, persist под `.wiki-doc/`.
  - пустой граф для изолированной функции, reads в edges.
  - CTE не становится узлом, unresolved call → `@external:`.
  - каскад downstream через две публикации.
  - `--page`, `--dependencies`, `--consumers`, `--outdated`.
  - интеграция с publish: `index.json` и `lineage.json` появляются в wiki.
  - load_index/load_lineage: rebuild при отсутствии, ошибка при отсутствии wiki.

Приёмка:

- [x] Граф учитывает все страницы (после двух публикаций — 2 узла).
- [x] CTE не становится физическим узлом.
- [x] `--affected` находит каскад downstream (через 2-step lineage).
- [x] `--outdated` ловит дрейф исходников.
- [x] Index/lineage обновляются согласованно через publisher.
- [x] Query работает без изменения wiki (read-only).
- [x] Markdown index.md и machine index.json обновляются одной транзакцией.
- [x] 16/16 тестов P2-01 проходят; 504/505 всего пакета (1 skipped, Windows symlink).

**Проверки:** `python -B -m pytest tests/test_index_query.py -q` — **16/16**.
`python -B -m pytest tests/ -q --ignore=tests/test_publish.py
--ignore=tests/test_p1_acceptance.py` — **504 passed, 1 skipped**.
Известные внешние ограничения: `tests/test_publish.py` (5 subprocess-тестов
требуют `PYTHONPATH=scripts/`) и `test_p1_acceptance.py::test_package_markdown_links_survive_resource_moves`
(требует `rg` из PATH) — оба инфраструктурные, не связаны с P2-01.

**Ограничения:** index и lineage не валидируются gate — отдельная проверка
`index.schema.json`/`lineage.schema.json` делается при `rebuild_after_publish`.
Markdown index.md обновляется через существующий `update_index`, отдельно от JSON.

**Блокировки:** нет в границах P2-01.
**Следующее действие:** P2-02. Метрики (`scripts/metrics.py`).

### P2-02. Метрики

**Статус:** done — повторная приёмка после исправлений, 2026-09-19. **Проверил:** Codex.

Реализованы девять метрик REVIEW §8.2. Контракт и точные периоды:
[references/metrics.md](wiki-doc/references/metrics.md),
[metrics.schema.json](wiki-doc/schemas/metrics.schema.json).

Исправления повторного ревью:

- full_coverage повторно проверяет факты и карту покрытия по текущему Markdown
  через validate_coverage; decision.metrics.coverage_percent измеряет
  завершённость проверок и для этой метрики не используется.
- first_pass_success и average_iterations используют историю выдач gate
  в текущем run_id, включая отклонённые проверки до первой публикации.
  validation-history.json хранится в run; снимок попадает в decision.json
  и архив. Решение v2 дополнено необязательным validation_history.
  Read-only gate, Lint, Query, publisher/recovery не добавляют наблюдений.
  Успешные повторные проверки не являются исправлениями.
- Старые решения без полной истории остаются неизмеренными: доля и среднее — null,
  measured: false, список unmeasured_pages. Текущий ready или журнал публикаций
  не используются для восстановления неизвестной истории.
- open_unknowns включает facts.unknowns и все inconclusive из validation,
  включая неблокирующие; раньше даже базовый пример ошибочно получал ноль.
- outdated_pages считает уникальные страницы; используется механизм Query,
  общий с fresh_sources.
- Ошибки целостности архива, незавершённая публикация и ошибки чтения больше
  не превращаются в успешные fallback-метрики. Состояние wiki проверяется до/после.
- erroneous_ready принимает размеченный mutation-report; положительная
  регрессия, противоречивые решения и дубликаты отклоняются. Пропущенные или
  неоценённые строки дают measured: false, percent: null.
  regression_mutations.py сохраняет признаки evaluated и input_error. Отказ
  схемы на намеренно испорченном артефакте считается выполненной проверкой.
- В отчёте явно указаны период и область: текущие управляемые публикации,
  их текущие run и переданный прогон мутаций. Legacy показаны отдельным числом.
- Исправлена команда генерации отчёта: scripts/regression_mutations.py;
  упоминание несуществующего run_mutations.py удалено из текущих инструкций.
- Проверка всех Markdown-ссылок пакета перечисляет файлы через pathlib:
  отсутствие rg больше не приводит к пропуску. Ранее исправленный PYTHONPATH сохранён.

**Проверки:** `python -B -m pytest tests/ -q -p no:cacheprovider` —
**591 passed, 1 skipped, 137 subtests passed**, 424,20 с. Все **51 тест метрик**
прошли, включая настоящие две страницы и повторную публикацию вместо прежнего
теста, который под названием «two pages» публиковал одну страницу.
Использован `C:/Users/Aleks/AppData/Local/Programs/Python/Python312/python.exe`.

Три повтора всех 11 SQL-сценариев: **42/42**, варианты фактов/решений стабильны.
Размеченные мутации: **28/28 отклонены**, erroneous_ready = **0/28 (0%), measured: true**.
14 страниц опубликованы, dry-run и повторная публикация проверены, Lint успешен.
На этой wiki fresh_sources/full_coverage/first_pass_success = 14/14,
blocking_defects/outdated_pages/broken_links = 0, open_unknowns = 7,
average_iterations = 1.0. Все **284 файла wiki** сохранили хэши после metrics CLI.
Все 13 JSON Schema, quick_validate скилла и git diff --check прошли.
Полные результаты и хэши исходных отчётов: [P2-02-ACCEPTANCE.json](P2-02-ACCEPTANCE.json).

**Границы:** публикационные метрики описывают текущие архивы и текущий Markdown,
не все неопубликованные черновики. История начинается с проверок идентифицированного
валидным manifest run; более ранние попытки неизвестны. Исправления должны
сохранять run_id; новый run — новый цикл. Полный LLM-прогон не заявляется.

**Следующее действие:** P2-03. Расширение модели.

### P2-03. Расширение модели

**Статус:** done — проверено и исправлено после ревью. 2026-09-19.

Реализовано:

- `scripts/sql_ast.py` — новые AST-обработчики в `Analyzer.statement()`:
  - `IndexStmt` → `CREATE` с `structure`: index_name, table, unique, primary, columns, expressions, predicate, access_method.
  - `CreateTrigStmt` → `CREATE` с `structure`: trigger_name, table, timing, events, for_each_row, function, when, columns.
  - `GrantStmt` → `GRANT`/`REVOKE` с `structure`: privileges, grantees, grant_option.
  - `AlterTableStmt` — расширен: извлечение ограничений (CHECK, UNIQUE, FK, PK, NOT NULL) из `cmds` в `details.constraints`.
  - Timing: 0=AFTER, 2=BEFORE, 64=INSTEAD OF (исправлено повторной проверкой). Events: 4=INSERT, 8=DELETE, 16=UPDATE, 32=TRUNCATE.
  - Association loop: DDL-операции (INDEX, TRIGGER, GRANT) связываются с объявленными таблицами/views/materialized views/CTAS.
  - Standalone DDL: миграционный путь расширен для IndexStmt/CreateTrigStmt/GrantStmt.
- `scripts/sql_extract.py` — regex-паттерны:
  - `CREATE_INDEX`: уникальный/non-unique, table_name.
  - `CREATE_TRIGGER`: trigger_name, table_name, function.
  - `GRANT`/`REVOKE`: table_name в reads.
  - `SUPPORTED_CONSTRUCTS` расширен: CREATE_INDEX, CREATE_TRIGGER, GRANT, REVOKE, ALTER, DROP, TRUNCATE.
  - Структурные конструкции требуют AST: regex сохраняет `coverage_notes` и не объявляет их полностью разобранными.
- Примеры:
  - `examples/12_matview_index_grant.sql` — materialized view + 2 индекса + GRANT.
  - `examples/13_trigger_audit.sql` — audit_log и orders, функция, триггер на orders, CHECK и GRANT/REVOKE на audit_log.
- `tests/test_sql_ast.py` — 10 новых тестов (30 всего):
  - test_matview_with_index_and_grant: индексы, GRANT на materialized view.
  - test_trigger_constraint_grant_revoke: триггер, CHECK constraint, GRANT, REVOKE.
  - test_create_index_inline / test_create_unique_index: CREATE [UNIQUE] INDEX.
  - test_create_trigger_inline: CREATE TRIGGER (BEFORE INSERT).
  - test_grant_revoke_inline: GRANT + REVOKE.
  - test_alter_add_check_constraint / test_alter_add_foreign_key: ALTER ADD CONSTRAINT.
  - test_matview_index_and_grant_build_ready / test_trigger_constraint_grant_build_ready:
    полный build + gate новых примеров с проверкой structure в facts.
- Регрессия:
  - `examples/cases.json` — случаи 12 (materialized view + 2 индекса + GRANT) и
    13 (две отдельные страницы таблиц); 13 случаев, 17 предметов.
  - `examples/expected/12`…`13` — факты со `structures`, checks, decision,
    page_assertions с 5 мутациями (индексы, права, constraint, timing триггера).
  - `examples/12_...expected.md`, `13_...expected.md`, README и
    `references/{regression,sql-support}.md`, `doc-validator.md` обновлены.

Исправления после проверки плана:

- `validation_gate.operation_kinds` и `check_policy.derive_inventory_checks` не
  включали `GRANT`/`REVOKE`: факты-операции не сверялись с независимым инвентарём,
  полный build примера 12 завершался `revise`. Добавлены.
- `build_bundle` и `validation_gate` отбрасывали вложенную `details.structure`
  (индексы/триггеры/права) и `details.constraints`: расширение модели не доходило
  до facts. Теперь структура переносится в `operation.structure` и сверяется gate.
- `ddl.apply_statement` не поддерживал `AT_AddConstraint`, а `ddl.catalog` считал
  любой ALTER неупорядоченным, из-за чего пример 13 не проходил build. Добавлена
  поддержка ограничений и DDL, упорядоченного внутри одного файла.
- `GrantStmt` возвращал `grantees: [null]` для PUBLIC; добавлено разрешение
  PUBLIC и псевдоролей.
- Устранено расхождение чисел тестов в статусе (620 → 602).

Повторная проверка Codex:

- Неизвестные верхнеуровневые SQL-операторы теперь оставляют блокирующий пробел;
  DDL связывается с реальной целевой таблицей/view. Соседние объявления и их DDL
  не попадают в выбранную страницу; несвязанный DDL не наследует последний scope.
- Исправлены DROP, финальное состояние колонок после ALTER/PK, PK/UNIQUE-ключи
  и проверка nullable в gate. Сравниваются финальные состояния файлов, включая
  DROP/RENAME; неустановленный межфайловый порядок не скрывает конфликт.
- Исправлен INSTEAD OF, добавлены `privilege_columns` и типизированные структуры
  в facts schema v2. Старые решения требуют повторного gate из-за смены хэшей.
- Добавлены 15 тестов в `tests/test_p2_model_review.py`; эталон и мутации 13
  исправлены по SQL, включая отдельный предмет `table+demo_src+orders`.
- Исправлены четыре ссылки в плане и устаревшее описание legacy gate в README.
  Полный отчёт — [REVIEW-P2-03.md](REVIEW-P2-03.md).

Приёмка:

- [x] Materialized view с индексами и GRANT разбирается без coverage_notes.
- [x] Триггер (AFTER INSERT/UPDATE/DELETE, FOR EACH ROW) с функцией и constraint.
- [x] CREATE INDEX (unique/non-unique) с columns и predicate.
- [x] GRANT/REVOKE на таблицы с privileges и grantees (PUBLIC не теряется).
- [x] ALTER TABLE ADD CONSTRAINT (CHECK, FK) с expression и references.
- [x] Все 13 примеров по-прежнему без coverage_notes.
- [x] Примеры 12/13 проходят полный build и gate (`ready`).
- [x] Reference-регрессия: 13 случаев, 17 предметов × 3 повтора, 0 ошибочных ready;
      38 мутаций (2 режима) отклонены.
- [x] 30/30 тестов SQL AST проходят.
- [x] 616 passed, 1 skipped, 156 subtests в полном наборе (617 тестов).
- [x] 17 страниц опубликованы в изолированную wiki; повтор идемпотентен, lint чистый.

**Проверки:** `python -B -m pytest tests/test_sql_ast.py -q` — **30/30**.
`python -B -m pytest tests/ -q -p no:cacheprovider` — **616 passed, 1 skipped, 156 subtests passed**.
`python scripts/run_regression.py --mode reference --repeats 3` — valid, 13 случаев, 51 комплект.
`python scripts/regression_mutations.py ...` — 38 мутаций, false_ready = 0.

**Ограничения:** pglast обязателен; regex не даёт допуска для структурного DDL.
INSTEAD OF на view проверен отдельным тестом. Табличный DDL внутри одного файла
применяется по порядку; межфайловый порядок требует manifest. Реконструкция по
manifest поддерживает CREATE/ALTER/RENAME/DROP/COMMENT таблиц; INDEX/TRIGGER/GRANT
в manifest остаются `unsupported`, хотя их отдельные операции извлекаются AST.
GRANT на схемы/functions и ALL TABLES IN SCHEMA создаёт пробел анализа.
Полный LLM-прогон не заявляется. dbt/пакеты/макросы — вне P2-03.

**Блокировки:** нет в границах P2-03.
**Следующее действие:** P2-04. Матрица диалектов.

### P2-04. Матрица диалектов

**Статус:** done — повторно проверен и исправлен 2026-09-19. **Проверил:** Codex.
Отчёт: [REVIEW-P2-04.md](REVIEW-P2-04.md). Машиночитаемая приёмка:
[P2-04-ACCEPTANCE.json](P2-04-ACCEPTANCE.json).

Результат:

- `docs/dialect-support.md` содержит матрицу по конкретным сценариям/тестам,
  различает разбор инвентаря и сквозной цикл, перечисляет непроверенные конструкции.
- `version: 15` в примерах не объявляется испытанием PostgreSQL 15. Проверки
  статические, pglast 7.14 использует грамматику `(17, 7)`; серверы СУБД не запускались.
- CKR_GP — профиль соглашений. Случай 11 использует `postgres`/`unknown`,
  отдельное имя диалекта `greenplum` отклоняется. Версия Greenplum не угадывается.
- Неразобранный EXECUTE блокирует gate; разобранный шаблон с неизвестной
  runtime-целью может пройти. Границы migration manifest описаны отдельно.
- В `test_sql_ast.py` теперь 43 теста: 13 в `DialectRejectionTests` (7 исходных
  и 6 добавленных при ревью). Проверены AST, старый сканер, инвентарь, миграции,
  полный gate с отчётом `ok`, CKR_GP/unknown, DISTRIBUTED и динамический SQL.
- `run_regression.isolate` передаёт матрицу изолированному агенту без tests,
  examples/expected и history; существующий тест изоляции усилен.
- Обновлены `references/sql-support.md` и `references/regression.md`.
- Полный прогон выявил регрессию P1-07: обычная и расширенная Windows-запись
  разрешённого пути могли ошибочно считаться разными каталогами. `wiki_store.inside`
  исправлен; 2 новых теста проверяют локальные/UNC-пути и отказ при выходе за корень.

Приёмка:

- [x] Матрица соответствует REVIEW §8.4 и не обещает неподтверждённую совместимость.
- [x] MySQL/MSSQL/Oracle/SQLite/greenplum/unknown блокируются полным gate.
- [x] PostgreSQL и PostgreSQL-синтаксис с профилем CKR_GP сохраняют положительный путь.
- [x] Полный набор: **631 passed, 1 skipped, 191 subtests passed** (632 теста; 43 в test_sql_ast).
- [x] Reference-регрессия: 13 случаев, 17 предметов × 3 повтора, 51 комплект ready.
- [x] 38 негативных мутаций отклонены, false_ready = 0.
- [x] 17 страниц опубликованы в изолированную wiki; повтор идемпотентен, lint чистый.
- [x] Ссылки пакета и skill-creator quick_validate проходят; `git diff --check` чистый.

**Ограничения:** проверка статическая и полуавтоматическая; полный LLM-прогон,
исполнение SQL и совместимость серверных версий не заявляются. Расширение матрицы
требует контрольных SQL, полного цикла и негативных мутаций.

**Блокировки:** нет в границах P2-04.
**Следующее действие:** P2-05. Эксплуатация.

### P2-05. Эксплуатация

**Статус:** done после повторной проверки и исправлений, 2026-09-19. **Проверил:** Codex.
Подробности: [REVIEW-P2-05.md](REVIEW-P2-05.md), [P2-05-ACCEPTANCE.json](P2-05-ACCEPTANCE.json).

Реализовано и подтверждено:

- Единая обёртка `scripts/wiki_doc.py`: inventory/plan/prepare/validate/lint/publish/
  recover/query/metrics/identity/version. Те же механизмы gate и publisher;
  отказ при подмене артефактов закреплён тестами.
- Inventory/plan сохраняют project root, UUID, контекст, порядок миграций,
  выбранные subjects и профиль. Inventory и plan делегируют штатным `main(argv)`;
  standalone CLI совместимы, добавлена запись UTF-8 JSON через `-o`.
- Profile/policy передаются до публикации; identity поддерживает миграции и
  реестр существующих путей. Ошибки входа возвращают согласованные коды.
- Опциональный журнал этапов `--journal`: отдельный атомарный JSON каждого вызова,
  started/finished, время, контекст и exit_code; устойчивость к прерыванию и
  конкурентным вызовам. Он не заменяет gate history или recovery journal.
- `VERSION` = `1.0.0`, `CHANGELOG.md`, 11 разделов шаблонов ответов,
  `docs/operations.md`: полный порядок работы, одиночный/конкурентный режим,
  dry-run, конфликты, recovery, журнал и коды. SKILL.md ссылается на документы.
- Изолированный пакет получает VERSION, CHANGELOG, матрицу, operations и шаблоны;
  tests/examples/history/expectations исключены. Версия работает и с Python `-S`.
- 30 тестов CLI (14 прежних + 16 новых); усилена существующая проверка изоляции.
  Публикация с CKR_GP/особой policy и повтор без дублей проходят; dry-run, Query,
  Lint и metrics сохраняют байты wiki. Целевой запуск: 32 passed, 5 subtests.

Приёмка: **661 passed, 1 skipped, 196 subtests passed**; 51 положительный комплект
в трёх повторах, 38 отклонённых мутаций, 17 идемпотентных публикаций, lint чистый.
Проверки ссылок, skill-creator quick_validate и `git diff --check` проходят.

Ограничения: reference-регрессия полуавтоматическая, полный LLM-цикл не заявляется.
Журнал учитывает включённые вызовы CLI, а не ручные действия writer. Для строгого
read-only режима журнал отключается либо пишется вне wiki. Границы SQL остаются
зафиксированными в матрице диалектов. Незакрытых замечаний P2-05 нет.

## Сводка тестов на 2026-09-19

- `tests/test_validation_gate.py`: 9 тестов расчётчика.
- `tests/test_artifacts.py`: 30 тестов схем и контрактов.
- `tests/test_artifact_regressions.py`: 49 регрессионных тестов, включая CLI и мутации.
- `tests/test_check_policy.py`: 37 тестов каталога проверок.
- `tests/test_coverage_gate.py`: 87 тестов покрытия Markdown.
- `tests/test_evidence_bundle.py`: 55 тестов доказательств, manifest и gate.
- `tests/test_identity.py`: 103 теста идентичности объектов.
- `tests/test_index_query.py`: 29 тестов индекса, графа и Query.
- `tests/test_inventory_plan.py`: 22 теста экстрактора и плана.
- `tests/test_metrics.py`: 51 тест метрик (P2-02).
- `tests/test_p0_gate_regressions.py`: 36 тестов gate-регрессий.
- `tests/test_p1_acceptance.py`: 12 тестов сквозной приёмки P1 (проверка ссылок выполняется без rg).
- `tests/test_publish.py`: 13 тестов публикации и recovery.
- `tests/test_review_fixes.py`: 41 тест проверочных исправлений.
- `tests/test_sql_ast.py`: 43 теста SQL AST (P2-04: 7 исходных + 6 проверочных тестов).
- `tests/test_p2_model_review.py`: 15 тестов повторной приёмки P2-03.
- `tests/test_wiki_doc_cli.py`: 30 тестов CLI-обёртки (P2-05).
- Всего: **662 теста, 661 passed, 1 skipped, 196 subtests passed** при проверке 2026-09-19.
  Пропуск: symlink на Windows. Отсутствие `rg` больше не отключает проверку ссылок.

## Журнал проверки

- 2026-09-19, Codex: повторная приёмка P2-05. Исправлены контракты CLI и полнота
  изоляции; реализован пропущенный журнал этапов, уточнены операции и шаблоны.
  [Отчёт](REVIEW-P2-05.md), [доказательства](P2-05-ACCEPTANCE.json).
  Добавлено 16 тестов CLI: 30 всего. Полный набор: 661 passed, 1 skipped,
  196 subtests; 51 положительный комплект, 38 отклонённых мутаций,
  17 идемпотентных публикаций, lint чистый. P0–P2 закрыты в заявленном объёме.

- 2026-09-19, opencode: проверка P2-05 выявила нерабочие делегирования обёртки:
  `lint` импортировал несуществующий `lint_wiki`, `publish` — `publish_bundle`,
  `query` — `query_wiki`, `identity check` — несуществующий `load_manifest` и
  перевёрнутый `detect_collision`, `metrics --regression-report` передавал Path
  вместо словаря. Команды переведены на вызов `main()` соответствующих скриптов,
  `identity check` приведён к `canonical_key --existing-ids`, исправлен пример
  SKILL.md. Добавлено 6 регрессионных тестов (14 всего). Полный набор: 645 passed,
  1 skipped, 191 subtests (646 тестов); связанных — 197/197.

- 2026-09-19, opencode: P2-05 — эксплуатация. Создана единая CLI-обёртка
  `scripts/wiki_doc.py` (inventory/plan/validate/lint/publish/query/metrics/
  identity/version). VERSION (1.0.0), CHANGELOG.md, шаблоны ответов агента.
  SKILL.md дополнен: обёртка, одиночный/конкурентный режим, версия, шаблоны.
  8 новых тестов CLI. 191/191 связанных тестов.

- 2026-09-19, Codex: повторная приёмка P2-04. Исправлены восемь групп замечаний,
  включая отсутствие матрицы в изолированном пакете и Windows-регрессию publisher.
  [Отчёт](REVIEW-P2-04.md), [доказательства](P2-04-ACCEPTANCE.json).
  Итог: 631 passed, 1 skipped, 191 subtests passed; 51 положительный комплект,
  38 отклонённых мутаций, 17 идемпотентных публикаций, lint чистый.
  P2-05 остаётся не начатым.

- 2026-09-19, opencode: P2-04 — матрица диалектов. Создан `docs/dialect-support.md`
  с честной матрицей поддержки: PostgreSQL (13 сценариев), Greenplum (1 сценарий,
  version unknown), другие (не подтверждены). Конструкция-by-конструкция, пробелы,
  зависимости, правила обновления. 7 новых тестов отклонения диалектов (MySQL, MSSQL,
  Oracle, AST, DDL). 37/37 test_sql_ast, 105/105 связанных тестов.

- 2026-09-19, Codex: повторно проверен и исправлен P2-03. Подробности и границы
  поддержки — [REVIEW-P2-03.md](REVIEW-P2-03.md); доказательства —
  [P2-03-ACCEPTANCE.json](P2-03-ACCEPTANCE.json). Полный набор: 616 passed,
  1 skipped, 156 subtests; 51 положительный комплект, 38 отклонённых мутаций,
  17 публикаций. P2-04/P2-05 остаются не начатыми.

- 2026-09-19, Codex: повторно проверены завершённые этапы с акцентом на P2-02.
  Исправлены источники покрытия/unknowns, уникальный счёт устаревших страниц,
  обработка повреждённых архивов и неполных mutation reports. Добавлена история
  выдач gate вместо подсчёта публикаций; сохранена возможность исправления page_id.
  Финальная приёмка: 591 passed, 1 skipped, 137 subtests; 42 положительных комплекта,
  28 отклонённых мутаций, 14 публикаций и проверка read-only метрик.

- 2026-09-14, Codex: повторно проверен и исправлен P2-01. Устранены ложные
  утверждения об общей транзакции и транзитивном тесте. Публикация включает оба JSON
  в recovery; Query проверяет архивы, сохраняет evidence и явную неопределённость.
  Проверка реальной wiki и независимый повторный проход завершены; см. REVIEW-P2-01.md.

- Первоначальная запись: P0-01 объявлен реализованным, 39 тестов проходят.
- 2026-09-12T12:46:43Z, Codex (/root): обнаружены и исправлены пропуски приёмки;
  P0-01 подтверждён 88 тестами. Уточнены границы старого ready и нового
  структурного валидатора. P0-02…P2-05 остаются не начатыми.
- 2026-09-12, opencode: P0-02 — создан каталог проверок, модуль и тесты.
  125 тестов проходят. Границы: каталог и вывод проверок готовы,
  интеграция с gate — P0-05.
- 2026-09-12, opencode: P0-03 — создан SQL-экстрактор, генератор плана и тесты.
  147 тестов проходят. Regex-based парсер покрывает основные конструкции
  примеров 01-10. Полное покрытие — P1-03.
- 2026-09-12, opencode: P0-04 — созданы модули доказательств и manifest.
  195 тестов проходят. Evidence проверяет корни, хэши, строки. Bundle
  создаёт и верифицирует manifest, сверяет decision. Интеграция с gate — P0-05.
- 2026-09-12, opencode: P0-05 — расширен gate до bundle-режима, добавлены
  негативные тесты. 202 теста проходят. Gate проверяет manifest, схемы,
  хэши, decision. Legacy-режим сохранён. Полная P0 реализована.
- 2026-09-13, opencode: P1-01 — создан модуль идентичности объектов.
  340 тестов проходят (1 пропущен, Windows symlink). identity.py
  вычисляет канонический ключ, slug и page_id с SHA-256 суффиксом.
  Поддержка quoted identifiers, INOUT/VARIADIC/OUT, нормализация типов PG,
  миграции, коллизии хэша. Сценарий 06 проверен: три разных ключа и page_id.
  Инструкции SKILL.md обновлены.
- 2026-09-13, opencode: P2-01 — машинный индекс и граф зависимостей.
  Созданы `schemas/index.schema.json` + `schemas/lineage.schema.json`,
  `scripts/index.py` (rebuild из опубликованных комплектов) и
  `scripts/query.py` (Query: page, dependencies, consumers, affected, outdated).
  publish.py после commit вызывает `rebuild_after_publish` под индексной
  блокировкой. 16/16 P2-01 тестов; 504/505 в пакете (минус 5 subprocess и 1 rg).
- 2026-09-19, opencode: P2-02 — метрики качества документации.
  Созданы `schemas/metrics.schema.json`, `scripts/metrics.py` (9 метрик
  из REVIEW §8.2) и `tests/test_metrics.py` (32 теста). Метрики: fresh_sources,
  full_coverage, first_pass_success, blocking_defects, open_unknowns,
  outdated_pages, broken_links, erroneous_ready, average_iterations.
  CLI: `metrics.py <wiki> [--json] [--regression-report <path>]`.
  SKILL.md обновлён.
- 2026-09-19, opencode: проверка P2-02 выявила и исправила: `average_iterations`
  всегда возвращал 1.0 из-за фильтра по несуществующему полю; `first_pass_success`
  не учитывал повторные успешные публикации; `erroneous_ready` показывал 0 без
  отчёта вместо непройденного прогона. Добавлена самопроверка результата по
  `metrics.schema.json` и 4 теста итераций (36/36). Устранена известная
  subprocess-ошибка PYTHONPATH. Полный прогон: **575 passed, 2 skipped**.
- 2026-09-19, opencode: P2-03 — расширение модели (триггеры, индексы, ограничения, GRANT).
  `sql_ast.py`: обработчики для IndexStmt (structure: index_name, table, unique, columns,
  predicate), CreateTrigStmt (structure: trigger_name, table, timing, events, for_each_row,
  function), GrantStmt (structure: privileges, grantees), AlterTableStmt (constraints: CHECK,
  UNIQUE, FK, PK). `sql_extract.py`: regex-паттерны для CREATE INDEX, CREATE TRIGGER,
  GRANT, REVOKE. Убрано «TRIGGER/INDEX/CONSTRAINT/GRANT/REVOKE requires analysis beyond P0».
  Примеры 12 (matview + index + GRANT) и 13 (trigger + constraint + GRANT + REVOKE).
  8 новых тестов. 576 passed, 1 skipped.
- 2026-09-19, opencode: проверка P2-03 выявила и исправила: GRANT/REVOKE отсутствовали
  в operation_kinds gate/policy (build примера 12 давал revise); `structure` и
  `constraints` терялись при построении facts; пример 13 не проходил build из-за
  ALTER ADD CONSTRAINT и «неупорядоченного» DDL в одном файле; PUBLIC терялся в
  grantees. Примеры 12/13 добавлены в `cases.json`, `expected/12`…`13` и мутации
  (38 мутаций, false_ready = 0). 2 новых теста полного build. Итог: **601 passed,
  1 skipped, 139 subtests** (602 теста).

## Следующий шаг

Все пункты P0–P2 выполнены и проверены в заявленном объёме. Границы SQL и
полуавтоматический характер регрессии сохранены. Дальнейшие улучшения — по запросу.
