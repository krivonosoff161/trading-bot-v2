# BACKLOG — Идеи не для текущей фазы

Сюда записываем всё что "хорошая идея но не сейчас".

**Правило ревью:** Claude проверяет этот список каждые 2-3 дня в начале сессии.

**Последнее ревью:** 2026-05-17

**Условные обозначения:**
- ✅ Внедрено в прод
- 🧪 Протестировано, отклонено (с причиной)
- ⏳ Запланировано, не начато
- ❌ Закрыто (устарело / нет смысла)

---

## ✅ ТЕКУЩИЙ ЛУЧШИЙ БЭКТЕСТ (эталон)

**Дата:** 2026-05-03 (D2+B3)
**Git commit:** `91c1807` (движок) / `86fb111` (HEAD)
**Период:** 63 дней, 5 пар (BTC/ETH/SOL/XRP/DOGE) + ADA кэш

### DRIFT с D2+B3 фильтрами:
| Метрика | Значение |
|---|---|
| Сигналов | **146** |
| WR | **89%** |
| Profit Factor | **3.51** |
| Симуляция | **+144.1%** |
| Макс. просадка | **6.3%** |

### История улучшений:
| Изменение | Коммит | Эффект |
|---|---|---|
| V6C ranging_recovery | c2262fd | sim +90%→+105%, +9 сигналов |
| FAST hold 150m→90m | 73278a4 | sim +107%→+114%, DD 5%→4% |
| BB FADE not_thrust+slope_fading | 088cd7b | фикс P1 (5 убыточных DOGE) |
| D2: trigger vol < 0.9× baseline | 91c1807 | DRIFT WR 74%→89% |
| B3: ETH veto UTC 22-01 | 91c1807 | DRIFT n 158→146 |

### Параметры прода:
- FAST hold: **90m** (240m ночью), SWING hold: **300m**
- SL: `sl_k × 1.2 × ATR_15m`
- FAST TP1: 0.8R (TRENDING/RANGING) / 0.4R (DRIFT)
- ADX period: **9** (из config.yaml)
- BB FADE пары: BTC, ETH, DOGE (XRP/SOL отключены — PF < 1)

---

## P1 — Pump Engine: профиль входа для ресурсов и акций

### ⏳ Отдельный профиль входа для commodities/stocks в pump engine
- Скринер уже мониторит CL-USDT-SWAP (нефть) и другие non-crypto SWAP пары
- Текущие пороги входа (1m vol_spike + price_move) настроены под крипто-альткоины
- Нефть/золото/акции двигаются медленнее — нужен отдельный профиль: другой таймфрейм (5m/15m), другие пороги
- **Когда:** после стабильной статистики на крипто (Phase C pump engine)

---

## P0 — Ближайшие практические задачи

### ✅ BB Fade как отдельный WS процесс (ws_bb_fade.py) — 15.05.2026
- MTF BB Fade: 15m setup (BB touch) + 5m entry (wick rejection)
- Бэктест v3: WR=70.6%, avg=+0.478%, PF=1.89, 344 сигнала, MIN_WIDTH=2.0%
- Фильтры: RSI sell≤60/buy≥40, vol<1.5, bw≥2%, skip Asia UTC 00-06
- Запущен в start_all.bat, stop.bat, сигналы в Telegram + logs/bb_fade/
- Лейблер: scripts/analysis/bb_fade_label_outcomes.py

### ⏳ BB Fade: интеграция режимных правил (Phase F.2+)
- ws_bb_fade.py запущен как отдельный процесс ✅
- Следующий шаг: весовые правила RANGING/TRENDING/DRIFT; DRIFT — уменьшить TP1
- **Когда:** после накопления 50+ live сигналов из ws_bb_fade.py

### ⏳ BB Fade: tape pre_buy_ratio фильтр (Гипотеза F.2)
- Анализ bt_bb_tape_analysis.py: pre_buy_ratio 0.5-0.7 → WR=75%; <0.3 или >0.7 → WR=0%
- Данные: 5-мин окно перед входом из E:\trading-data\ticks\
- Нужно: tape_recorder копит данные → после 30+ дней данных проверить гипотезу на live сигналах
- **Когда:** когда tape_recorder накопит 30+ дней данных (ожидание ~июнь 2026)

### ⏳ Переработка пользовательской системы (LLM промт + Telegram UI)
- Текущая проблема: LLM не знает о режимах (TRENDING/DRIFT/RANGING), два параллельных источника сигналов, мёртвый `_scanner_loop()` в telegram_bot.py
- Что нужно: новый режим 5 для BB FADE в промте, осознание TRENDING/DRIFT в тексте, чистый Telegram UI без legacy кода
- **Зависит от:** ws_main_screener проработал 24-48ч в shadow-режиме → есть что оценивать
- **Когда:** следующая сессия после анализа логов main_screener

### ⏳ Pump Engine: добавить alt-coin пары
- Текущие пары (BTC/ETH/SOL/XRP/DOGE) слишком большие — памп 2%+ редкость
- 13 сделок за несколько дней, нужно больше активности
- Кандидаты: AI-USDT, PENGU-USDT и другие активные альты
- **Когда:** после 50+ сделок с текущими парами ИЛИ если активность < 1 сигнал/день

### ⏳ analyze_signal_log.py — полный прогон
- Скрипт написан, но нужно 100+ labeled сигналов
- **Когда:** как только накопится достаточно данных

---

## P0 — Технический долг из аудита 16-17.05

### ⏳ Перенос bt_pump_*.py в scripts/backtest/
- 8 файлов research toolkit лежат в scripts/ws/ (нарушение CLAUDE.md)
- Файлы используют relative imports через sys.path.insert на свою папку
- Нужна отдельная аккуратная сессия: один импорт за раз + smoke test
- Зависимость на production уже устранена (fetch_ctvals вынесен в src/exchange/okx_meta.py)
- **Когда:** в отдельную сессию когда будет 1-2 часа без других задач

### ⏳ CB state persistency в pump orchestrator
- Сейчас `cb_daily_pnl` и `cb_halted` хранятся только в памяти
- После рестарта (watchdog или вручную) защита от убытков ОБНУЛЯЕТСЯ
- Риск: после краша в плохой день можно сразу опять зайти в убытки
- Нужно: сохранять state в `logs/pump/cb_state.json` + читать при старте
- **Когда:** до перехода в Phase D (real trading) — обязательно

### ⏳ Screener-to-orchestrator silence alert
- Если ws_screener_live упадёт и перестанет писать active_universe.json — оркестратор живёт без сигналов часами молча
- Нужно: heartbeat-check на возраст файла; если >5 мин → log ERROR + Telegram alert
- **Когда:** до Phase D

### ⏳ _calc_rsi в ws_bb_fade — слишком короткое окно
- Сейчас берёт `closes[-(period*3):]` для RSI(14) — это 42 свечи
- Wilder smoothing стабилизируется за 5×period = 70 свечей
- Результат: первые RSI значения смещены
- Решение: использовать `src.strategy.indicators.calc_rsi` на полном буфере
- **Когда:** после 50+ BB Fade live сделок (если WR не дотягивает до 65%)

### ⏳ Документация: пути backtest_simulate.py
- docs/BACKTEST_ENV_REFERENCE.md, docs/drift_test_map.md ссылаются на `scripts/backtest/backtest_simulate.py`
- Реально файл в `scripts/archive/backtest_simulate.py` (был перенесён)
- Нужно: либо обновить пути в docs, либо вернуть файл (зависит от того, нужен ли он сейчас)
- **Когда:** при следующем запуске бэктеста — кто-то наткнётся

### ✅ Tech audit + dead code archived (16.05.2026)
- v1/v2 pump engines → scripts/archive/
- pump_engine: секция config.yaml — DEPRECATED
- 6 хардкодов в оркестраторе → config (pending_ttl_sec, confirm_vol_min_ratio, stagnation_*, eviction_*, ban_hours)
- _check_position_live, expire_sec dead code — удалены
- src/exchange/okx_meta.py — fetch_ctvals вынесен из bt_pump_filters
- Commits: 0d0d38f, 1b31ab5

### ✅ Telegram rate-limit fix (17.05.2026)
- Проблема: 293 NOTIFY ok в группу за ночь → физически доставлено только 1 (Telegram silent drop при бурсте)
- Фикс: per-chat asyncio.Queue + worker с 2с min interval; send_message_to проверяет ok field + retry на 429
- Архитектурное: pump шлёт ТОЛЬКО в группу (extra_notify_chats), личные чаты убраны (личка зарезервирована за анализатором)
- Commit: 06503f5, 1b31ab5

---

## P1 — После закрытия B.5 + S2.3

### ⏳ Оркестратор основного сканнера
- По аналогии с pump orchestrator: сигнал → auto-open → live SL/TP мониторинг каждую секунду → auto-close
- Сейчас: сигнал уходит в Telegram → человек входит вручную → выход вручную. Никакого автоматического мониторинга нет
- Что нужно: отдельный `ws_signal_executor.py` — читает сигналы из main_signals.jsonl, открывает позиции, мониторит через _on_candle_update, закрывает по SL/TP
- AUTO_TRADE=false → режим paper (уже есть в auto_execute.py), позже true
- **Когда:** после того как ws_main_screener shadow → prod (закрытие S2.3) + WR подтверждён на 100+ сигналах

### ⏳ Data Recording System (архитектура готова — GPT 11.05.2026)

**Ключевой инсайт:** `compute_signal()` уже вычисляет всё (ADX/slope/BB/OBI/engine_vars по всем TF) — `ws_main_screener._maybe_emit_signal()` просто выбрасывает 80% при записи.

**Три компонента по приоритету:**

1. **Signal Snapshot** (быстро, ~20 мин) — добавить `json.dumps(result)` в `_maybe_emit_signal()` → `logs/signals/signal_snapshot.jsonl`. Единый `signal_id` для signal → snapshot → label.

2. **Per-candle Feature Log** (средне) — `src/data/feature_writer.py`, хук в WSFeed на закрытие 5m/15m/1H/4H, плоский CSV → `logs/features/{tf}/{symbol}/{date}.csv.gz` + `_index.jsonl`.

3. **Tick Recorder на HDD** (тяжело) — переработать `scripts/analysis/tape_recorder.py`: путь из `.env` (`TAPE_DATA_DIR=D:\trading-data\ticks`), per-symbol файлы, 30 пар, исправить `start_tape.bat` (неверный путь к скрипту).

4. **analysis_query.py** — `scripts/analysis/analysis_query.py`: берёт `signal_id` → находит snapshot → per-candle features ±30m → ticks ±5m → один DataFrame для анализа. Поддерживает фильтры: `--where "regime=='DRIFT' and outcome=='SL'"`.

**Новые файлы:** `src/data/feature_writer.py`, `src/data/snapshot_writer.py`, `scripts/analysis/analysis_query.py`
**Изменить:** `signal_engine.py` (расширить 4H индикаторы), `ws_main_screener.py`, `ws_scanner.py`, `tape_recorder.py`
**Когда:** после закрытия B.5 + S2.3, первым делом — Signal Snapshot (минимум кода, максимум пользы)

### ⏳ WS тестер — прогон WS-архитектуры на истории
- Текущий backtest_simulate.py работает на REST свечах (batch). WS-движок (ws_main_screener) тестируется только в лайве
- Идея: построить WS-тестер который воспроизводит WS-поток на исторических данных → прогоняет signal_engine → выдаёт те же метрики что и backtest_simulate.py
- Ценность: можно быстро тестировать изменения в signal_engine без ожидания 24-48ч лайв данных
- GPT уже в проекте — можно ему поставить задачу построить ws_backtester.py
- **Когда:** после закрытия S2.3, параллельно с оркестратором основного сканнера

---

## P1 — После замены основного сканнера на теневой (ws_main_screener)

### ⏳ Переработка LLM промтов и правил под новую архитектуру
- Текущие промты (llm_formatter.py) заточены под старый REST сканнер
- При переходе на ws_main_screener: новые поля (regime, FVG, vol_ratio, detected_on), новые стили (BB_FADE)
- Пересмотреть правила форматирования под каждый стиль: FAST/SWING/BB_FADE
- **Когда:** сразу после решения о переключении на ws_main_screener

### ⏳ Переработка клиентского бота под новый поток сигналов
- Текущий Telegram бот ожидает формат старого скринера
- Новый скринер даёт больше контекста: режим, таймфрейм, FVG, стиль
- Обновить: шаблоны сообщений, кнопки обратной связи, история запросов
- **Когда:** одновременно с переработкой LLM промтов

---

## P2 — Telegram канал ридер (Telethon)

### ⏳ Скрипт чтения Telegram каналов для сбора торговых идей
- Библиотека: Telethon (Python, Telegram API)
- Каналы: True_Market_Vision, MidChart, EuphoriaHL, Sokolov_TTFM, web3memoriess, uiartemzvezdin, Nat_Selection, O4racta1
- Сохранять посты локально → анализ паттернов и идей
- **Когда:** после закрытия Phase C (WR>60%)

---

## P1 — Ночной стоп-хант паттерн (Asian session)

### ⏳ Ликвидационный стоп-хант 00:00–06:00 UTC
- Паттерн: резкий памп/дамп на 15m в азиатскую сессию (низкая ликвидность)
- BTC и альты делают ложный пробой BB → собирают стопы → разворот
- Часы: 00:00–03:00 UTC (03:00–06:00 МСК) и 21:00–00:00 UTC (00:00–03:00 МСК)
- Торговая идея: ждать пика/дна движения → войти на BB reverse → TP середина
- Связан с BB Fade концепцией но специфичен по времени суток
- **Нужно:** бэктест по часам UTC на 15m данных за месяц, минимум 30 паттернов
- **Когда:** после Phase C pump engine

---

## P1 — BB Fade переосмысление

### ✅ BB Fade как самостоятельный скальп — реализован 15.05.2026
- ws_bb_fade.py: MTF 15m+5m, WR=70.6%, PF=1.89, запущен в прод (paper)

---

## P1 — После 100+ labeled сигналов

### ⏳ ETH-specific recalibration
- ETH системно слабее (B3 фильтр уже добавлен для DRIFT часы 22-01)
- Проверить: нужен ли дополнительный ETH-specific порог ADX или vol
- **Когда:** после полного прогона analyze_signal_log.py

### ⏳ Late Momentum Entry Filter
- Источник: SOL SELL 01:46 UTC — вход на хвосте состоявшегося импульса
- Гипотезы: distance_to_tp_consumed_pct, bars_since_trigger, price_extension
- **Когда:** 50+ TIME_EXIT кейсов → проверить корреляцию с late-entry

### ⏳ 1m Micro-Range Exhaustion Detector
- Диагностика протухшего импульса по поведению 1m цены после входа
- Метрики: 1m_range_width_10m, bars_crossing_entry, new_extreme_in_direction
- **Когда:** после late momentum filter research

### ⏳ Сессионный анализ WR по времени суток
- Группировка по hour_utc → таблица WR/PF/сигналов по часу
- Если паттерн есть → session_filter в compute_signal
- **Когда:** 100+ labeled сигналов

### ⏳ Daily Stop Limit
- Стоп после N стопов подряд за день, возобновление следующий UTC-день
- Счётчик consecutive_stops в scanner loop
- **Когда:** вместе с Reentry Guard (P3)

### ⏳ Публичный канал-отчётник со статистикой
- Telegram read-only канал: WR по парам/стилям, дневной итог
- Данные из signal_labels.jsonl — только закрытые позиции
- **Когда:** 100+ labeled сигналов — есть что показывать

---

## P1 — Signal Quality Research

### ⏳ SOL/ETH lead-lag correlation filter
- Наблюдение (10.05.2026): ETH даёт сигнал на 1-3 свечи раньше SOL при коррелированных движениях
- Идея: сигнал ETH (TRENDING/RANGING в одну сторону) → early-warning для SOL входа
- Применение: если ETH уже в позиции → SOL entry threshold ниже (или автоматически открывать)
- Риск: корреляция ситуативна, нужна статистика — сколько раз SOL следует за ETH
- **Когда:** 100+ labeled сигналов по обоим инструментам — проверить корреляцию

### ⏳ Constrained Strategy Optimizer (Optuna)
- Optuna перебирает параметры → backtest → устойчивые комбинации
- Только constrained: цель PF при DD ≤ Y, SL серия ≤ Z
- Requires: machine-readable JSON output из backtest (уже есть backtest_results_latest.json)
- **Когда:** после signal journal + microstructure edge

### ⏳ VIX + CME BTC COT как макро-фильтры
- VIX > 30 = risk-off, крипта падает → veto на LONG
- CME COT: позиции крупных спекулянтов → разворот
- **Когда:** 100+ labeled → проверить корреляцию macro с исходами

### ⏳ Дивергенция RSI как фильтр
- RSI-дивергенция на 1H/4H → блокировать SWING/TRENDING
- calc_rsi уже есть в indicators.py
- **Когда:** 100+ labeled → проверить корреляцию дивергенции с SL

### ⏳ Volume Profile — зоны справедливой стоимости
- VAH/VAL/POC: зоны с малым объёмом → цена проходит быстро
- TP2 уточнение через POC
- Данные: tape_recorder.py пишет с ~11 апреля
- **Когда:** достаточно tape данных

### ⏳ Cluster Search — зоны поглощения через объём + дельта
- delta = sum(buy_size) - sum(sell_size) за 1m/5m бар
- Применение: подтверждение входа
- **Когда:** 30+ дней tape данных

---

## P2 — Execution / Client Layer

### ⏳ Диалог с клиентом вокруг сигнала (Q&A режим)
- После сигнала клиент задаёт вопросы — LLM отвечает в контексте snapshot
- Нужно: хранить контекст последнего анализа per user
- **Когда:** первые реальные клиенты с активностью

### ⏳ Market Digest — суточный дайджест в Telegram-канал
- Автопост в 21:00 UTC: рынок сегодня / бот сегодня / завтра
- **Когда:** после закрытия S2.3

### ⏳ Premium анализ по скрину (/deep команда)
- Вариант Б: Gemma/Qwen text-only, расширенный промпт, три сценария
- Новая кнопка или /deep команда
- **Когда:** после накопления первых клиентов

### ⏳ Client Execution Reconciliation
- Клиент присылает CSV из OKX → importer матчит к signal_id
- **Когда:** первые платящие клиенты с реальной историей

---

## P3 — Infrastructure

### ⏳ BB FADE на старших таймфреймах (1H/4H)
- BB FADE 5m убыточен на XRP/SOL; 1H бар — более весомое событие
- Бэктест: BB(20, 2.0) на 1H → mean reversion к mid за 4-8 баров
- **Когда:** после закрытия S2.3 (30+ labeled)

### ⏳ Reentry Guard (Anti-Churn)
- Кулдаун: 60s та же сторона, 180s после убытка
- **Когда:** вместе с Daily Stop Limit

### ⏳ Pattern Engine — пинбары / поглощения / inside bars
- Лёгкий детектор, возвращает (bias, confidence, strength)
- TA-Lib содержит 60+ паттернов свечей — подключается одной функцией
- **Когда:** как дополнительный фильтр входа

### ⏳ Pattern Recommendation Engine (отдельный инструмент)
- Отдельный движок (не внутри бота): пара + таймфрейм → TA-Lib scan → "обнаружен молот на поддержке, LONG, уровни X/Y"
- Детерминированный (не LLM), дополняет Concierge Analyzer
- **Когда:** после Pattern Engine фильтра, есть смысл если появятся клиенты

### ⏳ Pump Engine Phase C — Trend Accumulator Detector
- Текущий детектор: одна 1m свеча с price_pct ≥ 2% + vol_mult ≥ 2× → ловит только flash pump
- Проблема (05.05.2026): OL +23%, PENGU +12%, LAB +60% — трендовые пампы, не flash
- Добавить скользящее окно: 3-5 свечей подряд в одну сторону, суммарно ≥ 1.5-2% → трендовый памп
- Структура: импульс → зафиксировать уровень → ждать retest → отбой (пинбар) → вход
- SL под тень ретеста, TP продолжение импульса
- **Когда:** Phase C (после 50+ paper сделок с flash detector)

### ⏳ Pump Engine Phase C — Chain Re-entry + Breakeven Trail
- После входа: как только +0.5% → SL в безубыток (риск = только комиссия 0.1%)
- Закрылся по TP → тут же переоткрыться если тренд продолжается
- Каждый новый вход: SL выше предыдущего (trailing вверх)
- Работает и на retest: поймал "нож" → цена вернулась → SL в безубыток = нулевой риск
- Рождено из опыта ручного трейдинга на LAB/UB (02-04.05.2026) — слив = нет SL в безубыток
- **Когда:** Phase C, после trend accumulator detector

### ⏳ Graphify — оптимизация токенов с Claude Code
- github.com/nateraw/graphify — карта функций/зависимостей
- **Когда:** при ощутимом росте кодовой базы

---

## 🎯 Phase G — Multi-Agent LLM Architecture (большой горизонт)

> **Контекст:** обсуждено 17.05.2026 — стратегическая цель проекта после закрытия текущих фаз.
> **Концепция (пользователь):** перейти от rule-based скринера к системе LLM-агентов, обучаемых на собственных данных. Сейчас main screener — это "фотография рынка на 15m close", без памяти, без распознавания паттернов, без сравнения с историей. Phase G меняет это.

### ⏳ G.1 — Data Lake: единая схема и хранение
**Цель:** все события (signal/entry/outcome/tape) → единый parquet формат пригодный для обучения.

**Что есть сейчас (фрагментированно):**
- main_signals.jsonl (74) + main_signals_labels.jsonl (68)
- pump_signals.jsonl (487) + pump_labels.jsonl (464) — с MFE/MAE/R-кратными
- smart_pump_candidates.jsonl (138) — shadow mode
- bb_fade_signals.jsonl (1)
- signal_snapshot.jsonl (50) — full context
- E:\trading-data\ticks — ~50M тиков

**Что нужно построить:**
- `src/data/training_writer.py` — единая `dataclass TrainingRecord`:
  - `signal_id, ts, symbol, channel, side`
  - `context: {indicators, regime, mtf_alignment, btc_state, eth_state}`
  - `tape_window: {pre_5min, post_15min}` агрегат тейпа вокруг сигнала
  - `outcome: {TP/SL/TIME, hold_min, net_pct, mfe_r, mae_r}`
- Формат хранения: `data/training/{YYYY-MM-DD}.parquet`
- Backfill из существующих jsonl + tape archives
- Snapshot BTC/ETH/SOL **в момент любого сигнала** (market context для cross-asset)
- `scripts/data/load_training.py` → возвращает pandas DataFrame

**Объём:** ~1 неделя, без ML, чистая инфраструктура
**Когда:** после закрытия S2.3 + Phase C critical mass (200+ pump trades)

### ⏳ G.2 — Pattern Miner (бакетный анализ без ML)
**Цель:** найти устойчивые бакеты с WR >65% и фильтровать остальные.

```
для каждого record:
  bucket_key = f"{channel}|{regime}|{adx_bucket}|{vol_bucket}|{btc_state}|{hour_utc}"
  bucket_stats[bucket_key].update(outcome)

вывод: топ-50 бакетов с n>=20 и WR>=65%
```

**Применение:** перед каждым сигналом lookup в bucket_stats. Если `n>=10 AND WR>=55%` → ENTRY. Иначе → SHADOW (логируем, не торгуем).

**Эффект:** меньше сделок, выше качество. Тестируется бэктестом.

**Объём:** ~1 неделя
**Когда:** после G.1, требует 500+ labeled

### ⏳ G.3 — Multi-Agent LLM Pipeline
**Цель:** реализация пользовательского видения — несколько LLM с разными ролями.

| Агент | Вход | Выход | Стек |
|-------|------|-------|------|
| **Market Context** | BTC/ETH 4H + новости (CryptoPanic) | "медвежий/бычий/боковик + ключевые уровни" | Sonnet 4.6 |
| **Setup Analyzer** | Свечи пары + индикаторы + market context от #1 | "сетап + confidence 0..1 + ключевая зона" | Sonnet 4.6 |
| **Tape Reader** | Tape ±5 мин от точки входа | "buyers/sellers control + cluster zones" | Haiku 4.5 |
| **Risk Manager** | Outputs #1-3 + текущая позиция | "размер позиции + SL/TP с обоснованием" | Sonnet 4.6 |

**Финальное решение:** консенсус 3 из 4 агентов с confidence>=0.6 → ENTRY.

**Стоимость:** ~$0.5-2 за решение на Sonnet (5x дешевле Opus, ~95% качества для классификации). При 10 решениях/день = $5-20/день.

**Где живёт:** новый процесс `scripts/ws/ws_agent_orchestrator.py`. Параллельный канал, НЕ заменяет main/pump/bb_fade — добавляется поверх как третий слой фильтрации.

**Объём:** 2-3 недели
**Когда:** после G.1 + G.2 (нужна data для промптов)

### ⏳ G.4 — Fine-tuning open-source модели на исходах
**Цель:** локальная модель учится на наших данных, заменяет API на повторяющихся задачах.

**Подход:**
- Open-source база: Qwen 72B или Llama 70B
- LoRA fine-tune на закрытых сделках: `(context) → (outcome)`
- Дешёвый inference: TogetherAI ~$1/M токенов или своё железо
- Метрика: prediction calibration (когда модель сказала confidence=0.8 → 80% сделок TP)

**Зачем не Anthropic fine-tune:** Anthropic не предоставляет fine-tune для широкой публики. OpenAI/Together поддерживают LoRA на open models.

**Объём:** 1-2 месяца (включая инфра под inference)
**Когда:** после 1000+ labeled через G.3

### Критерий для перехода в Phase G
- S2.3 закрыта (100+ main labeled, WR>=80% на последних 30)
- Phase C закрыта (200+ pump, WR>=60%, PF>=2.0)
- F.1 закрыта (50+ BB Fade live, WR>=65%)
- **Только тогда** имеет смысл строить мета-слой. Без работающих моделей на старте — построим эпициклы вокруг ложной картины.

---

## P4 — Монетизация / S3

### ⏳ Сайт (Вариант А)
- Yandex Cloud VM, читает сигналы которые бот посчитал
- Блокер: нет универсального кода для произвольных пар
- **Когда:** 100+ labeled + стабильный движок

### ⏳ Мультипользовательская система (Copy-trading)
- Клиент подключает OKX → бот торгует по сигналам на его счёте
- **Когда:** S2, 2-3 недели реальной статистики

### ⏳ Funding Rate Арбитраж (дельта-нейтральный)
- Funding > 0.1% → шорт фьючерс + лонг спот → собираем funding
- **Когда:** после AUTO_TRADE

### ⏳ ML Signal Engine (ensemble)
- RandomForest + GradientBoosting на исторических результатах
- **Когда:** 500+ labeled сигналов

---

## Архив — Уже реализовано ✅

- ✅ DRIFT режим (core стратегия)
- ✅ Бэктестер на исторических свечах (backtest_simulate.py)
- ✅ Авто-сканер рынка (telegram_bot.py, _scanner_loop — код есть, не запускается)
- ✅ Telegram уведомления
- ✅ Автоматическое исполнение ордеров (auto_execute.py — выключено, нет средств)
- ✅ Signal log pipeline (signal_log.jsonl + label_outcomes.py)
- ✅ Батники Windows (start/stop/clear/logs/update_journal)
- ✅ OVERALL report в бэктесте
- ✅ candle_vol_delta фильтр (soft delta veto)
- ✅ V6C ranging_recovery (коммит c2262fd)
- ✅ BB FADE: not_thrust + slope_fading (коммит 088cd7b)
- ✅ FAST hold 150m→90m (коммит 73278a4)
- ✅ D2+B3 DRIFT фильтры (коммит 91c1807)
- ✅ Premium Screenshot Analysis — Gemma 3 27B, 5 категорий
- ✅ Вкладка "Реальные сделки" в journal.xlsx (build_journal.py)
- ✅ label_outcomes.py — авто-лейблинг через OKX fills-history
- ✅ analyze_signal_log.py — написан (ждёт 100+ labeled)
- ✅ WebSocket инфраструктура (ws_pump_engine.py, ws_scanner.py)
- ✅ bt_sweep_drift.py + bt_param_sweep.py — sweep harness
- ✅ Библиотека промптов (три сценария в WAIT/NO_TRADE) — коммит 11125c5
- ✅ Persistent keyboard "🔍 Анализ" в Telegram
- ✅ bt_entry_filters.py — sweep 14 фильтров × 5 hold, TP1→BE→TP2 структура (08.05)
- ✅ TRENDING FAST FVG фильтр + hold_trending_fast_minutes=120 (08.05)
- ✅ WSFeed candle4H + per-bar буферы (09.05)
- ✅ ws_main_screener.py — shadow-mode WS скринер, 29 пар (09.05)
- ✅ ws_bb_fade.py — MTF BB Fade WS процесс, 15m+5m, WR=70.6%, PF=1.89 (15.05)
- ✅ bb_fade_label_outcomes.py — лейблер BB Fade сигналов (15.05)
- ✅ tape_recorder.py в start_all.bat — автозапуск tape recorder (15.05)
- ✅ Pump notifications в Telegram community group (13.05)

---

## Архив — Протестировано и отклонено 🧪

### 🧪 Independent ATR TP/SL from entry price (2026-04-07)
- independent entry ± ATR: WR 81%→57%, PF 2.64→1.53, DD 17.7%→28.7%
- TIME_EXIT снизился, но SL вырос — не улучшение

### 🧪 BB + Volume 1m/5m скальп (2026-04-08)
- Пользователь вручную 13/13 прибыльных за сессию — но бэктест на 5m BB: WR 65.5%, PF 4.79
- BB FADE внедрён как Канал 2, работает на BTC/ETH/DOGE

### 🧪 DRIFT entry phase detection (2026-04-04) — 3 гипотезы:
- TP1 geometry veto: DRIFT 695→54 сигналов (-92%) — уничтожает канал
- BB compression after impulse: не срабатывает ни разу за 56 дней
- Trigger extension veto: WR 66%→60%, баланс +181%→+103% — режет лучшие сигналы
- Вывод: проблема в качестве режима DRIFT, не в точке входа

### 🧪 Cadence и freshness (2026-04-05):
- ATR_1H вместо ATR_15m для SL: WR 72%→74%, баланс +181%→+135% — нет
- Hold FAST 480m/SWING 720m: WR 72%→63%, PF 1.78→1.20 — нет
- 5m polling cadence: WR/PF идентичны, mfe +27% — без фильтра дублей бесполезно
- 1m freshness filter: TP и TIME_EXIT неотличимы по 1m структуре

### 🧪 DRIFT ADX min 12→8 (2026-04-05):
- PF 1.78→1.63, баланс +181%→+125% — зона ADX 8-12 это шум

### 🧪 Walking the band для BB FADE (2026-04-15):
- n=26, WR=46.2%, PF=1.05 — нестабильно по периодам; нужно 90+ дней

### 🧪 BB HTF фильтр (2026-04-12):
- Требует hold 480-720m + частичный выход — симулятор не поддерживает
- Отложено до правильной инфраструктуры

### 🧪 DRIFT baseline без D2+B3 (2026-04-25):
- n=158, WR=74%, sim=+87.7% — стало базой для D2+B3 сравнения

### 🧪 V-bottom гипотезы V1-V5C (2026-04-19):
- Все хуже baseline — DRIFT SHORT veto уничтожает эффект
- V6C ranging_recovery решил проблему через RANGING режим

---

## ❌ Закрытые идеи

### ❌ Claude Code slash-команды (2026-04-25)
- Поведение зашито в CLAUDE.md и SESSION.md — slash-команды избыточны

### ❌ Памп-сканер на MEXC фьючерсах
- Требует отдельный репо, интеграцию MEXC WS
- OKX pump engine уже решает ту же задачу на знакомой бирже
- Закрыто: дублирует pump engine, без дополнительной ценности пока OKX pump не изучен

---

## Отдельные проекты (не расширение текущего бота)

### Арбитражное направление
- Статистический арбитраж BTC→ETH лаг — нужен WS + tape данные
- Арбитраж альткоинов между биржами — требует 2 биржи
- Funding Rate арбитраж — требует AUTO_TRADE
- Новостной арбитраж — NLP + быстрое исполнение
- Polymarket информационный арбитраж — следить за запуском Polymarket Futures

### Smart Money / Microstructure
- Order Block + FVG — после накопления сигналов для бэктеста
- On-Chain Whale Tracking — Whale Alert API
- Складной метр (вложенные трендовые линии) — нужна база swing-уровней
- RSI Heatmap скринер — при расширении пар за текущие 5

### Мониторинг и UX
- **Pump Telegram уведомления (13.05.2026)** — OPEN/CLOSE события из ws_pump_orchestrator в Telegram. Формат как в логе: символ, направление, entry/SL/TP при открытии; исход+PnL+hold при закрытии. Реализация ~15 строк, не влияет на стратегию. Вернуться после WR>55%.

### Advanced Trading
- Связка опционы + фьючерсы — S3+, требует OKX Options API
- Order Book скальпер — отдельный репо, другая архитектура
- Flash Crash Bounce детектор — отдельный модуль
- Fear & Greed + новостной слой — после закрытия S1
- FADE / Mean Reversion режим — отдельный бэктест и SL/TP
