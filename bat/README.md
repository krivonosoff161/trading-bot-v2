# bat/ — пакетные файлы (.bat)

Редко используемые управляющие батники проекта. Пути починены под запуск из этой подпапки
(`cd /d %~dp0..` → рабочая директория = корень проекта).

**Часто используемые остаются в КОРНЕ** для удобства (привычка трейдера):
`start.bat` · `stop.bat` · `update_journal.bat` · `clear_cache.bat`.

Сюда переезжают редкие: `start_all` · `start_scanner` · `start_tape` · `start_telegram_bot` · `analyze_latest` · `collect_logs`.

См. `ARCHITECTURE.md` / `REFACTOR_PLAN.md`.
