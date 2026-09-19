# Эксплуатация wiki-doc

Единый вход из корня пакета: `python scripts/wiki_doc.py <command> [options]`.
Команды `inventory`, `plan`, `prepare`, `validate`, `publish`, `recover`, `lint`,
`query`, `metrics`, `identity`, `version` вызывают те же механизмы, что отдельные
скрипты. Аргументы конкретной команды: `python scripts/wiki_doc.py <command> --help`.
Пакет не установлен как Python-модуль; `python -m wiki_doc` из корня не является
поддержанным способом запуска.

## Одна страница

Создай UUID `<run_id>` и каталог `<run>` для артефактов одной страницы. Выбери
объявление `<scope>`, вычисли `<page_id>` через `identity compute`. Для миграции
используй `--kind migration --migration-path <relative.sql>` без schema/name.
Для существующих страниц передавай `--registry`, при необходимости `--legacy-keys`
и `--existing-ids`, чтобы сохранить согласованный путь.

```text
python scripts/wiki_doc.py inventory <project>/sql/object.sql --project-root <project> --run-id <run_id> --subjects <scope> --version <confirmed_version_or_unknown> --context <ddl.sql> -o <run>/inventory.json
python scripts/wiki_doc.py plan <run>/inventory.json --page-id <page_id> -o <run>/validation_plan.json
```

`inventory` сохраняет пути относительно project root, анализирует контекст и
принимает `--migration-manifest` с явным порядком. Для CKR_GP передавай один и тот
же `--profile <profile.json>` в `plan`, создание bundle, `validate` и `publish`.
Аналогично передавай явно выбранную `--policy` в plan/bundle/validate/publish.
Версия по умолчанию — `unknown`; UUID запуска следует задавать явно.

После плана создай facts, черновик и покрытие по [SKILL.md](../SKILL.md).
До содержательной проверки подготовь окончательные байты с ручными блоками:

```text
python scripts/wiki_doc.py prepare <run> <wiki>
```

Проверь подготовленный черновик, создай `validation.json` и manifest через
`bundle.py create`, перечислив те же SQL/DDL, профиль, политику и порядок миграций.
Подробный контракт — [artifacts.md](../references/artifacts.md).

```text
python scripts/wiki_doc.py validate <run> --root project <project> --root wiki <wiki> --write-decision --json
python scripts/wiki_doc.py publish <run> <wiki> --project-root <project> --dry-run
python scripts/wiki_doc.py publish <run> <wiki> --project-root <project>
```

Для профиля/особой политики дополни обе команды publish и validate теми же
`--profile`/`--policy`. `ready` разрешает публикацию, но не означает, что файл уже
опубликован. `--dry-run` проверяет комплект и показывает изменения пяти видов:
страница, Markdown-индекс, metadata, машинный индекс и lineage; wiki не меняется.
`--cleanup-run` удаляет только подтверждённый собственный `<wiki>/.tmp/<run_id>`.

Коды validate: 0 — ready, 1 — revise/blocked, 2 — ошибка входа. Inventory/plan:
0 — артефакт построен (включая возможные analysis gaps), 2 — ошибка входа;
построенный inventory сам по себе не даёт допуска. Publish/prepare/recover
сохраняют коды publisher: 0 — успешное выполнение, 1 — отказ/ошибка. Коды остальных
команд совпадают с их отдельными CLI. Не трактуй любой ненулевой код как `revise`.

## Журнал этапов

Включай журнал CLI явно, перед именем команды:

```text
python scripts/wiki_doc.py --journal <wiki>/.wiki-doc/activity/<run_id> inventory <sql> --project-root <project> --run-id <run_id> -o <run>/inventory.json
python scripts/wiki_doc.py --journal <wiki>/.wiki-doc/activity/<run_id> validate <run> --root project <project> --root wiki <wiki> --write-decision --json
```

Один JSON-файл с UUID на каждый вызов содержит версию пакета, команду, рабочий
каталог, ссылки на вход/выход, время начала и состояние `started`; после возврата команды — `finished`,
время окончания, `exit_code` и outcome. При прерывании запись остаётся `started`:
успех не предполагается. Файлы разных вызовов не перезаписывают друг друга.
Храни журнал вне `.tmp`, чтобы cleanup не удалил историю этапов.

Журнал начинается после разбора аргументов обёртки; синтаксическая ошибка её
собственного CLI не создаёт событие. Ошибка записи начального события запрещает
запуск команды. Ошибка финальной записи даёт код 2 и `command_exit_code` в stderr:
сама команда к этому моменту могла успешно опубликовать данные.

Это журнал вызовов, не доказательство корректности текста и не допуск к публикации.
История gate находится в `validation-history.json`, журнал восстановления publisher —
в `.wiki-doc/journal/`; они выполняют отдельные функции. Ручные действия writer
подтверждаются артефактами и содержательным отчётом, а не событиями CLI.
Без `--journal` обёртка не пишет этот журнал. Для строго read-only Query/Lint/metrics
не указывай его либо укажи каталог вне проверяемой wiki. То же относится к dry-run.

## Конкурентная работа и восстановление

Одиночный режим использует тот же протокол публикации. При нескольких агентах
каждая страница получает отдельные run_id и каталог артефактов. Publisher берёт
общую блокировку, повторно проверяет комплект и снимки, затем согласованно
обновляет страницу, оба индекса, граф и metadata. Одновременные добавления разных
страниц допускаются после проверки безопасного дополнения индекса.

Конфликты возможны при изменении источников, страницы, реестра идентичности,
ручной правке индекса или истечении ожидания блокировки. Разные страницы не
означают отсутствия таких конфликтов. Для одной страницы согласуй очередность.
Внешний редактор может не соблюдать блокировку; чужая правка не перезаписывается.

```text
python scripts/wiki_doc.py recover <wiki>
```

Незавершённые транзакции также проверяются перед новой публикацией. Recovery
сохраняет явный конфликт и резервные копии, если безопасный откат невозможен.
Подробности — [publication.md](../references/publication.md).

## Версия и отчёт пользователю

`version` выводит значение [VERSION](../VERSION); история — [CHANGELOG.md](../CHANGELOG.md).
При отсутствии VERSION выдаётся `0.0.0-dev`, а не фиктивная версия релиза.
Эти файлы, данная инструкция и [шаблоны ответов](agent-response-templates.md)
передаются изолированному адаптеру вместе с матрицей диалектов. Tests, expectations
и history остаются вне его входного пакета.
