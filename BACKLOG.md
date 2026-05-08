# BACKLOG — Идеи не для текущей фазы

Сюда записываем всё что "хорошая идея но не сейчас".

**Правило ревью:** Claude проверяет этот список каждые 2-3 дня в начале сессии.

**Последнее ревью:** 2026-05-08

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

### ⏳ ADA в бэктест
- В PAIR_PARAMS уже есть, в backtest SYMBOLS нет
- Прогнать с ADA → обновить эталон
- **Когда:** следующая сессия

### ⏳ Pump Engine: добавить alt-coin пары
- Текущие пары (BTC/ETH/SOL/XRP/DOGE) слишком большие — памп 2%+ редкость
- 13 сделок за несколько дней, нужно больше активности
- Кандидаты: AI-USDT, PENGU-USDT и другие активные альты
- **Когда:** после 50+ сделок с текущими парами ИЛИ если активность < 1 сигнал/день

### ⏳ analyze_signal_log.py — полный прогон
- Скрипт написан, но нужно 100+ labeled сигналов
- **Когда:** как только накопится достаточно данных

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

### Advanced Trading
- Связка опционы + фьючерсы — S3+, требует OKX Options API
- Order Book скальпер — отдельный репо, другая архитектура
- Flash Crash Bounce детектор — отдельный модуль
- Fear & Greed + новостной слой — после закрытия S1
- FADE / Mean Reversion режим — отдельный бэктест и SL/TP
