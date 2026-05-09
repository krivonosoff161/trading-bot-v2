# AI_COLLABORATION_PROTOCOL.md — Протокол работы с Qwen Coder

> Последнее обновление: 2026-05-10

---

## Цепочка работы

```
Пользователь формулирует задачу
    ↓
Claude Code готовит QWEN TASK (шаблон ниже)
    ↓
Пользователь вставляет задачу в Qwen Coder
    ↓
Qwen отвечает / делает branch / даёт отчёт
    ↓
Пользователь приносит ответ Claude Code
    ↓
Claude Code проверяет, интегрирует, коммитит
```

---

## Когда вызывать Qwen

- Second opinion по гипотезе (фильтр, параметр, режим)
- Review архитектуры или backtest-логики
- Проверка PLAN/BACKLOG vs код — есть ли противоречия
- Варианты параметров для sweep (без запуска)
- Черновик research-only скрипта в отдельной ветке
- Поиск рисков в отчёте или метриках
- Анализ кода на предмет технического долга

## Когда НЕ вызывать Qwen

- Задача требует локальных данных: `.pkl`, `logs/`, `journal.xlsx`, `SESSION.md`
- Быстрый локальный баг — дешевле сделать здесь
- Нужны реальные секреты или API ключи
- Запуск live/paper trading или бэктеста
- Задача маленькая (< 15 минут локально)

---

## Шаблон задачи для Qwen

```
QWEN TASK:

Context files:
- docs/AI_CONTEXT.md
- docs/REMOTE_DATA_MANIFEST.md
- docs/BACKTEST_ENV_REFERENCE.md
- [+ конкретные файлы по задаче]

Mode: READ-ONLY | BRANCH-ONLY
(READ-ONLY = только анализ, без изменений кода)
(BRANCH-ONLY = изменения только в feature/ ветке, не в main)

Task:
[описание задачи]

Expected output:
[что именно должен вернуть: markdown отчёт / код / таблица / список рисков]

Do not:
- запускать скрипты требующие локальных данных
- обращаться к .env, logs/, *.pkl
- предлагать AUTO_TRADE=true
- коммитить в main
```

---

## Примеры готовых задач

### Review sweep конфига

```
QWEN TASK:

Context files:
- docs/AI_CONTEXT.md
- docs/BACKTEST_ENV_REFERENCE.md
- scripts/backtest/bt_entry_filters.py

Mode: READ-ONLY

Task:
Проверь логику bt_entry_filters.py:
1. Корректно ли считается WR при включении TIME_EXIT?
2. Правильно ли реализована структура TP1→BE→TP2?
3. Есть ли риски в расчёте PF при малом n?

Expected output:
Markdown отчёт: найденные проблемы + конкретные строки кода.

Do not:
- запускать скрипт
- предлагать изменения в main без review
```

### Генерация sweep конфигов

```
QWEN TASK:

Context files:
- docs/AI_CONTEXT.md
- docs/BACKTEST_ENV_REFERENCE.md
- config.yaml

Mode: READ-ONLY

Task:
Предложи 8-10 комбинаций BT_* параметров для следующего sweep.
Текущий эталон: WR=88%, PF=3.92, sim=+161.3% (DRIFT, D2+B3+D3, hold=75m, tp1=0.5R).
Цель: улучшить sim при сохранении WR>80% и PF>3.0.

Expected output:
Таблица: параметры | гипотеза | ожидаемый эффект.

Do not:
- запускать бэктест
- использовать локальные данные
```

---

## Контекстные файлы Qwen (всегда включать)

| Файл | Назначение |
|---|---|
| `docs/AI_CONTEXT.md` | Архитектура, роли, что видно/не видно |
| `docs/REMOTE_DATA_MANIFEST.md` | Карта локальных данных |
| `docs/BACKTEST_ENV_REFERENCE.md` | Параметры, формулы, кэши |

---

## Правила интеграции ответа Qwen

1. Принести ответ Claude Code для проверки
2. Claude Code верифицирует логику и корректность
3. Если код — запустить `py_compile` и review
4. Коммит только после одобрения пользователя
5. Обновить `AI_CONTEXT.md` если изменилась архитектура
