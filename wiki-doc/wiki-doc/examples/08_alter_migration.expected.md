# 08 — восстановление схемы по manifest

Вход: migrations/manifest.json и только перечисленные в нём файлы. Пути manifest
относительны его директории. Документируем demo_migration.orders на ревизию 043.

- Порядок: baseline/orders.sql → 08_alter_migration.sql → migrations/043_amount.sql.
- Статус определения resolved, хотя файлов несколько.
- Итоговые колонки: id bigint PRIMARY KEY, amount numeric(18,4) DEFAULT 0,
  updated_on date.
- legacy удалена, total переименована; в итоговой структуре их нет.
- Комментарий amount взят из миграции 043.
- Изменение времени файлов не меняет результат, выбранный manifest.
- Дополнительный отрицательный сценарий: без manifest и без соглашения о порядке
  нельзя выбирать актуальную версию по mtime; результат ambiguous.

