# AI_CONTEXT.md — Контекст проекта для удалённых агентов

> Этот файл обновляется вручную перед передачей задачи Qwen Coder.
> Последнее обновление: 2026-05-16

---

## Проект

Trading Bot V2 — OKX фьючерсный скальпинг бот + Telegram Concierge Analyzer.
Язык: Python 3.10+. Биржа: OKX (SWAP фьючерсы, USDT-маржинальные).

**Текущая фаза:** S2.3 — Рост и качество сигналов.
**Git HEAD:** см. последний коммит в main.

---

## Роли агентов

| Агент | Где работает | Видит | Роль |
|---|---|---|---|
| Claude Code | Локально | Всё включая .gitignore | Архитектура, код, запуск, решения |
| GPT / Codex | Локально (VS Code) | Весь workspace | Анализ, черновики, review |
| Qwen Coder | Удалённо (GitHub) | Только tracked файлы | Review кода, гипотезы, sweep анализ |

---

## Что Qwen НЕ видит (но оно существует локально)

Подробная карта: `docs/REMOTE_DATA_MANIFEST.md`

Ключевые невидимые файлы:
- `SESSION.md` — живой контекст текущей сессии
- `logs/` — все runtime логи (очищены 09.05.2026, архив в logs_archive/)
- `logs/signals/signal_log.jsonl` — сигналы (начат заново 09.05)
- `logs/signals/main_signals.jsonl` — новый WS скринер (пишется с 09.05)
- `scripts/backtest/*.pkl` — кэши свечей для бэктеста (65d локально)
- `scripts/journal.xlsx` — Excel журнал с реальными сделками
- `.env` — API ключи (никогда не запрашивать)

**Правило для Qwen:** если файл не виден в GitHub — он не отсутствует, он в .gitignore. Запросить у пользователя sanitized summary.

---

## Текущая архитектура

### Запущенные процессы (start_all.bat):
1. `scripts/telegram_bot.py` — Telegram бот
2. `scripts/ws/ws_main_screener.py` — **shadow-mode**, 29 пар, пишет main_signals.jsonl
3. `scripts/ws/ws_screener_live.py` — все SWAP пары, пишет active_universe.json
4. `scripts/ws/run_pump_watchdog.py` → `ws_pump_orchestrator.py` — pump paper trading (Phase C)
5. `scripts/ws/ws_smart_pump.py` — новый pump движок shadow-mode (Phase C.1-C.5)
6. `scripts/ws/ws_bb_fade.py` — BB Fade mean reversion (Phase F.1)
7. `scripts/analysis/tape_recorder.py` — запись тиков на E:\trading-data\ticks

### Сигнальные каналы:
```
FAST   — 15m триггер, slope ≥30°, hold: DRIFT=75m / TRENDING=120m / иначе=90m
SWING  — 1H триггер, slope ≥30°, hold=300m
BB FADE— 5m триггер, RANGING/CHOPPY режим, hold=60m
PUMP   — WS 1m, alt-coins, ATR SL/TP, paper trading
MAIN WS— shadow-mode, 29 пар, ws_main_screener.py (без Telegram пока)
```

### Режимы рынка (detect_regime):
- **TRENDING**: ADX_4H ≥ 22 + ADX_1H ≥ 18
- **DRIFT**: ADX_1H 12-30, di_spread ≥ 5
- **RANGING**: боковик (иначе)
- **CHOPPY**: BB_width ≥ 3% + di_spread < 6

### Ключевые фильтры DRIFT:
- D2: veto если vol текущего бара < 0.9× baseline 15 баров
- B3: ETH-USDT veto UTC 22-01
- D3: BTC-USDT veto если vol_ratio > 3.0

### TRENDING FAST — FVG фильтр:
- `trending_require_fvg: true` в config.yaml
- Вход только внутри Fair Value Gap (3-свечной имбаланс, 15m, lookback=10)

---

## Актуальные исследовательские направления

1. **ws_main_screener shadow-mode** — работает с 09.05, нужно проанализировать main_signals.jsonl через 24-48ч
2. **Переработка LLM промта** — добавить режим BB FADE (5-й режим), осознание TRENDING/DRIFT
3. **Переработка Telegram UI** — убрать мёртвый _scanner_loop() из telegram_bot.py
4. **Delta alignment / NOT aligned** — проверить гипотезу, что SCANNER-сделки с delta NOT aligned дают лучшее качество, чем aligned. ADA уже есть в `SYMBOLS` основных backtest-скриптов; не предлагать повторно "добавить ADA", сначала сверять с кодом.

---

## Что уже устарело / закрыто

- `ws_pump_engine.py` (v1) и `ws_pump_engine_v2.py` — архивированы в `scripts/archive/` (16.05.2026). Заменены `ws_pump_orchestrator.py`.
- `pump_engine:` секция в config.yaml — deprecated, помечена комментарием. Прод читает `pump_orchestrator:`.
- `_scanner_loop()` в telegram_bot.py — мёртвый код, сканер переехал в ws_main_screener.py
- Стратегия E — закрыта, см. `docs/strategy_e_postmortem.md`
- ADX period 14 — заменён на 9 (config.yaml)

---

## Безопасные скрипты для Qwen (можно предлагать запуск)

```
python -m py_compile <файл>     # синтаксис
python scripts/project_snapshot.py  # статус (только чтение)
```

## Опасные скрипты (только локально, только с явного одобрения пользователя)

```
scripts/backtest/backtest_simulate.py  # требует локальный кэш + API
scripts/auto_execute.py                # реальные ордера (AUTO_TRADE=false сейчас)
scripts/telegram_bot.py               # живой бот с клиентами
```

---

## Как запрашивать локальные данные у пользователя

```
Мне нужны локальные данные которые не видны в GitHub:
- файл/паттерн: logs/signals/main_signals.jsonl
- зачем: анализ сигналов нового скринера
- нужные поля: symbol, regime, side, trade_style, ts
- формат: первые 20 строк или агрегат по режимам
```

---

## Ключевые метрики эталона (бэктест 07.05.2026)

- DRIFT + D2+B3+D3 + hold=75m + tp1=0.5R: **WR=88%, PF=3.92, sim=+161.3%**
- TRENDING FAST + FVG: **WR=57.5%, PF=3.61, sim=+28.9%** (n=40, hold=120m)
- BB FADE RANGING: **WR=62.5%, PF=1.98, avg_R=+0.318** (169 labeled)
