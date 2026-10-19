# Проверки этапа A

Дата: 2026-10-19. Windows, Python 3.13.2. Реальные календари и корпоративная
инфраструктура не подключались; COM Dispatch не вызывался. Установка Google SDK и
pywin32 использована только для проверки зависимостей; реальные вызовы отсутствуют.

- `python -m unittest discover -s tests -v`: **31 тест, OK**.
- `python -m compileall -q calendar_sync tests tools fixtures`: **OK**.
- Установка всех 23 закреплённых зависимостей в новый `.venv`: **OK**.
- Сборка и установка проекта с setuptools 80.9.0, без build isolation: **OK**.
- `uv pip check --python .venv\Scripts\python.exe`: **24 пакета совместимы**
  (23 закреплённых зависимости и сам проект).
- Fake CLI: dry-run — CREATE по одному на направление; apply — три копии;
  повторный apply — по одному SKIP, ноль CREATE/UPDATE/DELETE.
- `diagnose`: fake, Outlook UNVERIFIED, real_connections_attempted=false.
- `python tools\check_portability.py`: **PASS**, все 31 тест из распакованного
  ZIP в новом каталоге с кириллицей/пробелами. В ZIP только исходники/документы,
  ни local, ни config.json, ни venv. Проверены абсолютные пути пользователей,
  шаблоны секретов и отсутствие BOM. Это целевая статическая проверка, не
  доказательство отсутствия любых возможных секретов в произвольном файле.
- `git diff --check`: **OK**.

Тесты покрывают преобразования обоих направлений и двух источников Google,
All-day, Busy/privacy/reminders/Body, Free/declined/cancelled, пересечения,
нормализованные серии/исключения/перемещения, часовые пояса и DST, окно/историю,
идемпотентность, ручное изменение/удаление копии, Google tombstone и ambiguous
insert, дубли источника/маркеров, потерю SQLite, crash до commit, неполные снимки,
массовые удаления, test-ID scope, блокировку, ограниченные retries, безопасные логи
и независимое продолжение направлений. Fake/Google/COM mocks не заменяют приёмку
реальных провайдеров. Дальнейший порядок: HANDOFF.md.
