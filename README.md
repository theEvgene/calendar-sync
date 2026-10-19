# Calendar Sync v1.2 — этап A

Локальный CLI для Python 3.13. Исходные требования: [SPECIFICATION.md](SPECIFICATION.md).
Реальные Outlook COM и Google OAuth/API интеграция **не приняты**. Здесь проверяется
синтетическая синхронизация. Автозапуск по умолчанию отсутствует.

Откройте PowerShell в каталоге проекта. Python 3.13 должен быть установлен с launcher `py`.
Если launcher отсутствует, используйте путь к своему `python.exe` вместо `py -3.13`.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
```

Для последней команды нужен setuptools 80.9.0; установите его до сборки:

```powershell
.\.venv\Scripts\python.exe -m pip install setuptools==80.9.0
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation .
Copy-Item config.example.json config.json
.\.venv\Scripts\python.exe fixtures\seed_demo.py
.\.venv\Scripts\python.exe -m calendar_sync
.\.venv\Scripts\python.exe -m calendar_sync apply
.\.venv\Scripts\python.exe -m calendar_sync apply
.\.venv\Scripts\python.exe -m calendar_sync diagnose
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\check_portability.py
```

Ожидание: первый fake-apply создаёт три копии; повторный показывает только SKIP.
Fake-календари, SQLite и журналы находятся в `local/`, исключённом из Git.
`seed_demo.py` отказывается заменять существующий файл. Для нового опыта используйте
новую копию проекта. `dry-run` не меняет календари, SQLite или токены; блокировка
может создать отдельный файл `run.lock`. Блокировка автоматически снимается при
выходе процесса, включая аварийный. Один конфиг/набор календарей должен использовать
один lock и один SQLite; параллельные установки на разных ПК не поддерживаются.

Код возврата: 0 — успех; 1 — ошибка одного/нескольких направлений или событий;
2 — отказ запуска/конфига. Сообщения ошибок содержат только тип исключения,
чтобы не раскрывать текст приглашений и секреты. Локальный журнал apply ротируется
по 1 МБ, три резервные копии. В нём время, направление, SHA-256 ключ, действие и
тип ошибки; ни Body, ни заголовки, ни сырые ответы провайдеров туда не попадают.

Конфиг задаёт относительные пути от своего каталога. Для реального режима замените
`mode` на `real`, `timezone` на IANA-зону календаря Outlook, `shared_calendar` на ID
источника только для чтения; собственный календарь остаётся `primary`. В `local/`
на целевом ПК разместите OAuth authorized-user JSON (`token_file`) с доступом
Google Calendar events. OAuth consent и выдача токена выполняются локально;
программа не включает веб-сервер авторизации. JSON-токен не включайте в перенос.
Обновление access token выполняется только в памяти; refresh token должен оставаться
действительным. Real dry-run подключает оба адаптера только для чтения, поэтому его
разрешено запускать лишь на этапе B. `diagnose` никогда не подключает адаптеры.

Первые записи ограничиваются точными нормализованными ID синтетических источников:

```powershell
.\.venv\Scripts\python.exe -m calendar_sync dry-run --test-id 'SYNTHETIC_SOURCE_ID'
.\.venv\Scripts\python.exe -m calendar_sync apply --test-id 'SYNTHETIC_SOURCE_ID'
```

`--test-id` повторяется для нескольких ID. Google ID — API `event.id` экземпляра;
Outlook ID — GlobalAppointmentID, для повторения дополненный `:` и исходным временем
экземпляра в ISO UTC. Выбирайте ID тестовых событий локальным read-only скриптом;
рабочие события и их ID не передавайте обратно разработчику. Фильтр ограничивает
создание, обновление и удаление; отсутствие исходного тестового ID позволяет удалить
только его ранее связанную копию. Для real-apply нужно `policy_approved: true`.
Полный real-apply требует также `stage_b_accepted: true` и каждый раз явный
`--confirm-initial-fill`. Эти значения выставляет оператор после приёмки по HANDOFF.

`max_deletes` — абсолютный лимит удалений на направление (по умолчанию 5).
Превышение блокирует весь план направления. `--confirm-deletes` разрешает его после
просмотра dry-run. Неполное чтение источника отключает удаления независимо от флага;
неполная инвентаризация назначения блокирует все записи направления.

Windows Task Scheduler на этапе A не создаётся. После приёмки этапа B создайте задачу
`CalendarSync` в интерактивной сессии пользователя: вход в Windows и каждые 30 минут,
программа — абсолютный путь к `.venv\Scripts\python.exe`, аргументы —
`-m calendar_sync apply --config "<полный путь к config.json>" --confirm-initial-fill`,
рабочий каталог — каталог проекта. Запретите параллельные экземпляры, настройте повтор
при временном сбое; запускайте после готовности Outlook. Во сне/при выключенном ПК
синхронизации нет. Отключение существующего расписания:

```powershell
Get-ScheduledTask -TaskName CalendarSync | Disable-ScheduledTask
```

Переносите только исходники/документы и requirements, восстановите venv на новом ПК.
Не переносите `.venv`, `local`, `config.json`, OAuth или логи. Реальные интеграционные
ограничения и последовательность приёмки перечислены в [HANDOFF.md](HANDOFF.md).
