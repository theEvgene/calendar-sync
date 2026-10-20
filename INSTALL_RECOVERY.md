# Установка и восстановление на новом компьютере

Эта инструкция предназначена для оператора или другого агента. Она описывает только подготовку и synthetic-only проверку. Полное заполнение календарей и расписание требуют отдельного подтверждения после dry-run.

## 1. Получить исходный код

1. Клонировать `https://github.com/theEvgene/calendar-sync.git` в рабочий каталог.
2. Проверить ветку и последний коммит.
3. Не переносить из старого компьютера `local/google-token.json`, `local/state.sqlite3`, `local/sync.log` или `config.json` с секретами через чат или внешний носитель без согласованного защищённого канала.

## 2. Создать окружение

В PowerShell из корня проекта:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --index-url https://artifactory.tcsbank.ru/artifactory/api/pypi/python-all/simple -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

Если корпоративный индекс изменился, использовать только утверждённый внутренний адрес и не отключать TLS-проверку.

## 3. Подготовить конфигурацию

1. Скопировать `config.example.json` в локальный `config.json`.
2. Указать `timezone`, относительные пути `local/...`, собственный календарь и заранее согласованный ID общего календаря.
3. Оставить `policy_approved=true` только при наличии корпоративного разрешения.
4. Оставить `stage_b_accepted=false` до завершения всех приёмочных доказательств.
5. Проверить, что `config.json`, OAuth client JSON, token JSON и SQLite находятся под `.gitignore`.

## 4. OAuth Google

Создать OAuth desktop client в согласованном Google Cloud проекте и сохранить client JSON только локально как `local/google-oauth-client.json`. Добавить оператора в список test users, если приложение находится в режиме Testing. Запустить:

```powershell
.\.venv\Scripts\python.exe -m calendar_sync authorize-google --config config.json
```

Разрешить доступ только к согласованным календарям. Токен сохраняется локально в `local/google-token.json` и не публикуется.

## 5. Read-only и synthetic-only проверка

```powershell
.\.venv\Scripts\python.exe -m calendar_sync diagnose --config config.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Создать согласованные synthetic события вручную, получить их ID локальным helper-скриптом и передавать CLI только через повторяющийся `--test-id`. Не использовать реальные приглашения и не копировать их содержимое.

```powershell
.\.venv\Scripts\python.exe -m calendar_sync dry-run --config config.json --test-id <локальный-id>
.\.venv\Scripts\python.exe -m calendar_sync apply --config config.json --test-id <локальный-id>
```

Для Outlook → Google переносить только входящие встречи с `ResponseStatus=Accepted`; непредоставленные ответы, tentative и declined пропускать. Обычные личные записи и встречи, созданные оператором, не требуют ответа и остаются допустимыми.

## 6. Восстановление после потери SQLite

Остановить scheduler, если он включён. Сначала выполнить read-only dry-run. Для теста потери SQLite обратимо переименовать `local/state.sqlite3`, выполнить synthetic-only apply с тем же локальным allowlist, проверить отсутствие дублей и затем вернуть резервную копию. Если новый SQLite был создан во время теста, сохранить его только как диагностический артефакт; рабочий state возвращать после проверки.

Ожидаемый результат: существующие маркированные копии распознаются по inventory/marker, новые дубли не создаются, процесс завершается с кодом 0.

## 7. Перед публикацией и эксплуатацией

1. Выполнить `git diff --check` и полный тестовый набор.
2. Зафиксировать commit SHA и обезличенный acceptance-протокол.
3. Выполнить полный dry-run и отдельно показать оператору план удалений.
4. Только после отдельного подтверждения оператора разрешать первоначальное заполнение.
5. Scheduler включать отдельным действием после того, как `stage_b_accepted=true`.

## 8. Установить расписание и ручной запуск

После подтверждённого полного заполнения выполнить из корня проекта:

```powershell
.\scripts\install-scheduler.ps1 -WhatIf
.\scripts\install-scheduler.ps1
```

Проверить, что `CalendarSync` содержит 48 ежедневных триггеров от `00:00` до
`23:30`, а `CalendarSyncAtLogon` — один триггер при входе. Для обоих заданий должны
быть установлены `MultipleInstances=IgnoreNew`, `RestartCount=0` и
`StartWhenAvailable=false`. На рабочем столе должен появиться ярлык
«Синхронизировать календари».

Пропущенные во время сна или выключения запуски не восполняются. Ошибка не запускает
автоматический retry; следующий плановый запуск остаётся через 30 минут. Ручной ярлык
запускает тот же CLI и показывает его результат.
