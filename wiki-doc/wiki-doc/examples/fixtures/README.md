# Q-фикстуры — независимый контрольный набор (IMPROVEMENT Q-01)

Дата: 2026-09-23. План: [IMPROVEMENT-PLAN-2026-09-23.md](../../../IMPROVEMENT-PLAN-2026-09-23.md).

Компактные SQL-случаи, воспроизводящие конструкции и ошибки большого контрольного
объекта `ckr_uup_db_onboarding.sql`. Ожидания лежат в `../expected/qNN/` и написаны
**по исходному SQL вручную**, независимо от extractor и writer. Генератору они
недоступны: изолированный прогон (`scripts/run_regression.py`, режим `adapter`)
копирует в workspace только SQL/DDL и runtime скилла.

## Манифест

Регистрация случаев — [../cases-q.json](../cases-q.json). Отдельный манифест, а не
`cases.json`, чтобы исторический набор 01–12 (и его прогон `run_regression`) не
регрессировал до Q-08. Запуск проверки ожиданий:

```text
python -B -m unittest discover -s tests -p "test_q01_expectations.py" -v
```

## Изоляция

Как и в `examples/README.md`: подавать генератору только SQL/DDL одного случая и
его контекст. Никогда не передавать `../expected/**`, этот README с разбором
ошибок, `.expected.md` и `acceptance-large.json`.

## Случаи

| ID | SQL | Контекст DDL | Что проверяется | Ревью |
|----|-----|--------------|-----------------|-------|
| q01 | [q01_retro_param_local.sql](q01_retro_param_local.sql) | [q_context.sql](q_context.sql) | p_retro → v_retro → IF; историческая пара DELETE/INSERT; граница `fact_start_date < 2026-01-01` | D01 |
| q02 | [q02_null_check_sign.sql](q02_null_check_sign.sql) | q_context.sql | знак NULL-проверки `IS NOT NULL` | D02 |
| q03 | [q03_dedup_priority.sql](q03_dedup_priority.sql) | q_context.sql | пересекающиеся наборы is_done/is_phoned (5 и 3 кода), контрпример приоритета против ранней даты | D03 |
| q04 | [q04_date_branch.sql](q04_date_branch.sql) | q_context.sql | две ветви до/после `2026-01-01`, разные делители, привязка к `p_date`/`on_date` | D04 |
| q05 | [q05_gp_master_nested.sql](q05_gp_master_nested.sql) | q_context.sql + [q_context_gp.sql](q_context_gp.sql) | атрибут `EXECUTE ON MASTER`; вложенные таблицы; **репродуктор аварии**; неизвестная версия не доказывает совместимость | D05, D12 |
| q06 | [q06_cte_scopes.sql](q06_cte_scopes.sql) | q_context.sql | CTE `tm` против производной `s`; разные области одинаковых имён CTE в двух INSERT | D11 |
| q07 | [q07_xml_filters.sql](q07_xml_filters.sql) | q_context.sql | один XML-агрегат filters против пяти вызовов метаданных | D06 |
| q08 | [q08_positional_insert.sql](q08_positional_insert.sql) | q_context.sql + [migrations/](migrations/manifest.json) | позиционный INSERT и перестановка колонок миграцией 014 | D10 |
| q09 | [q09_kpi_levels.sql](q09_kpi_levels.sql) | q_context.sql | явные пары 10/100 и 20/200, разные наборы уровней (1,2)/(1), три расчётные ветви | D07, D08 |
| q10 | [q10_hours_plan.sql](q10_hours_plan.sql) | q_context.sql | нормирование часов `/8.0` и выбор плана по приоритету | D08 |
| q11 | [q11_rr_sl.sql](q11_rr_sl.sql) | q_context.sql | формулы RR и SL; дополнительный unknown расшифровки `rr` | D08, Q-07 |

## Положительные и отрицательные контроли

В ожиданиях десять случаев имеют целевой `ready`, а q05 до Q-03 — `blocked`.
Это ожидаемые решения, а не результат полного прогона Q-набора. Миграция q08
восстанавливается по manifest и проверяется на порядок и типы колонок.
«Всегда blocked» не считается улучшением качества: мутации из
`../expected/qNN/page_assertions.json` обязаны отклоняться по существу, а не по
наличию любого запрета.

## Матрица D01–D12

Представительные утверждения по D01–D12 — в `../expected/qNN/assertions.json`;
заготовки мутаций — в `page_assertions.json`. Полная сквозная матрица отказов
ещё не выполнена (Q-07). В частности, ожидание D12 о совместимости закреплено
в q05-A5; его проверка на сгенерированном тексте остаётся задачей Q-07/Q-08.

## Контрольный большой объект

[acceptance-large.json](acceptance-large.json) фиксирует SHA-256 входа и страницы,
приёмочные числа (77 INSERT / 77 DELETE / 1 UPDATE, 36 KPI, 68 пар, 30 источников,
3 ретро-пары) и политику хеша: при другом хеше прежние числа не применять молча.
Пути тестового проекта не являются константами runtime скилла.
