# REFACTOR PLAN — пересборка дерева (31.05.2026)

> Цель: порядок, понятный нескольким ИИ + будущим людям; НИ ОДНОГО потерянного файла; все пути/ссылки починены;
> чистый git. Принцип безопасности: **ничего не УДАЛЯЕМ — только перемещаем (под git) или архивируем-на-месте.** Всё откатываемо.

## Целевое дерево (итог)
```
trading-bot-v2/
├── ROADMAP.md ARCHITECTURE.md CLAUDE.md PLAN.md README.md  ← главные доки (один источник правды)
├── main.py config.yaml requirements.txt .env(.example)
├── bat/                         ← ВСЕ .bat сюда (с починкой путей)
├── src/
│   ├── exchange/ strategy/ data/ utils/   ← ❄️ движок + 🔧 ПОЛКА утилит (telegram/logger/llm_formatter/config/okx/indicators)
│   └── scout/                   ← 🟢 НОВАЯ СИСТЕМА (логика): collector, orchestrator, probe + __init__
├── logs/
│   ├── ... (движок: signals/features/...) ❄️ не трогаем
│   └── scout/                   ← 🟢 хранилище новой системы: signals/outcomes/training.jsonl + forward_series.csv
├── scripts/                     ← бот-скрипты (ws/analysis/...) ❄️ замороженo
│   └── analysis/research/        ← 📦 АРХИВ ~95 закрытых скриптов (АРХИВ-НА-МЕСТЕ, НЕ двигаем) + README-маркер
├── docs/                         ← своды/постмортемы/каталоги
└── обучение/                     ← эталоны разметки под ЛМ (reference)
```

## Что двигаем / что НЕТ (риск-контроль)
- 📦 **research/ (95 скриптов) — НЕ двигаем.** Их импорт-цепочки на жёстких путях (`importlib` spec_from_file_location) — перенос их РВЁТ. Архивируем-на-месте: README-маркер «archive, Main закрыт» + манифест active-vs-done. Ноль риска.
- 🟢 **3 активных скаута → `src/scout/`.** Они почти standalone (collector/probe — самостоятельны; orchestrator импортит collector). Перенос РЕАЛЕН и тестируем.
- ❄️ **Движок (`src/strategy`, `src/data/*_impulse*`), его `scripts/`, его логи — НЕ трогаем.**
- 📁 **Батники → `bat/`** (по твоей просьбе «по папкам»).

## ПУТИ/ССЫЛКИ к починке (главное — «не лоханись»)
| перемещение | что ломается | фикс |
|---|---|---|
| скауты → src/scout/ | вычисление `_ROOT` из `__file__` (глубина пути меняется!) | пересчитать parents[] в каждом |
| | взаимо-импорты (orchestrator→collector/probe) | поправить пути импорта |
| | `run_scout_daily.bat` путь к orchestrator | `scripts\analysis\research\` → `src\scout\` |
| | `digest_data/` путь | переключить на `logs/scout/` |
| батники → bat/ | в каждом `cd /d %~dp0` + относит. `scripts\` | `cd /d %~dp0..` (чтобы запуск шёл из корня) |
| | `start.bat` меню вызывает др. батники | обновить пути в меню |
| | CLAUDE.md «root has *.bat» | обновить правило структуры |

## Git-стратегия (чисто, твои коммиты целы)
- Существующие 3 unpushed коммита (`c666e21`,`6f91e5e`,`86aadf5`) — **НЕ трогаем** (без rebase/amend/reset).
- Рефактор = **отдельные коммиты по батчам**, каждый после теста.
- **Не пушим** в публичный remote без явного GO (ROADMAP/ARCHITECTURE раскрывают направление).
- Ничего не удаляем — `git mv` (история файла сохраняется).

## Порядок исполнения (батчами, ТЕСТ после каждого, потом коммит)
1. Скелет: создать `src/scout/`(+__init__), `bat/`, `logs/scout/`. Коммит.
2. Перенести `daily_digest_collector` → src/scout/, починить `_ROOT`, прогнать, коммит.
3. Перенести `orchestrator`+`probe`, починить взаимо-импорты + `run_scout_daily.bat`, прогнать, коммит.
4. Завести `logs/scout/` (signals/outcomes/training) + переключить запись. Коммит.
5. Батники → `bat/` + фикс `cd`/меню + CLAUDE.md. Прогнать каждый, коммит.
6. research/ → README-маркер «archive» + манифест active/done. Коммит.
7. Свести доки (ROADMAP=направление, ARCHITECTURE=дерево, PLAN/SERVICE_PIVOT — фолд/указатель). Коммит.
8. (позже, отдельно) перевод скаутов на полку Main (telegram/logger/llm_formatter).

## Граница
.env / AUTO_TRADE / config.yaml прод / движок — НЕ трогаем. Перемещения — только после GO трейдера, по одному батчу.
