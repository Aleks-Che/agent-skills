# Регрессионный цикл

[cases.json](../examples/cases.json) задаёт 11 случаев и 14 отдельных предметов страниц.
Для каждого случая в `examples/expected/<case>/` отдельно заданы факты, правила,
решение, утверждения страницы и негативные мутации. Эти ожидания не входят во вход
генератора. Они написаны по SQL и используются проверяющей стороной.

```text
python scripts/run_regression.py --mode reference --output <new_workspace> --repeats 3
python scripts/run_regression.py --mode saved --output <same_workspace> --repeats 3
python scripts/regression_mutations.py <workspace>/regression-report.json --expected-root examples/expected --output <new_mutation_dir>
python scripts/regression_publish.py <workspace>/regression-report.json --wiki <new_isolated_wiki>
```

`reference` — детерминированный технический адаптер `build_bundle.py`, а не испытание
LLM writer. Вместе с проверкой сохранённых результатов и независимым содержательным
проходом это явно обозначенный полуавтоматический цикл. Отчёт содержит режим, модель,
настройки, число повторов/исправлений, ошибки и число вариантов фактов/решений по предмету.
После изменений инструкций/шаблонов требуются минимум три прогона всех случаев.

Полный режим через адаптер агента:

```text
python scripts/run_regression.py --mode adapter --output <new_workspace> --repeats 3 --model <model-and-version> --settings "{}" --adapter "[\"agent-cli\",\"{request}\"]"
```

`--adapter` — JSON-массив argv; shell не используется. `{request}` заменяется путём к
JSON версии 1 с `skill_root`, `project_root`, `output_dir`, `sql`, `context`, `subjects`,
`version`, `profile` и `migration_manifest`. SQL/контекст копируются с сохранением
относительных путей; package содержит только runtime-инструкции, шаблоны и скрипты.
В рабочем каталоге нет tests, history или expectations. Это изоляция контекста,
не системная песочница для враждебного CLI. Адаптер должен соблюдать область входов.

Адаптер пишет `<output_dir>/runs.json`:

```json
{"schema_version":1,"runs":[{"subject":"<canonical-key>","run_dir":"0","profile":null}]}
```

Каждый run_dir разрешается внутри output_dir и содержит полный bundle. Все subjects
должны встретиться ровно один раз. Запуск ограничен `--timeout` (по умолчанию 120 секунд
на случай); stdout/stderr и параметры сохраняются. Ошибка/timeout/пропуск объекта —
неуспех. Доступ к внешнему агенту не предполагается автоматически; отсутствие полного
агентного цикла честно отражено в `full_agent_cycle_completed: false`.

Контракт `facts.page_contract = claims-v1` включает проверку видимых таблиц Markdown:
строки Fact/Property/SQL value сверяются с фактами и отдельными ожиданиями. Удаление
строки, подмена значения и согласованная подмена фактов/текста проверяются отдельно.
Содержимое sidecar не доказывает наличие утверждения в странице. Произвольные
пояснения вне контролируемых таблиц требуют независимого содержательного просмотра
по [doc-validator.md](../doc-validator.md); формальная проверка не заявляет понимания
любого текста. Нулевой false-ready относится к размеченному набору мутаций.
